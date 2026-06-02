"""Active service / version detection.

Risk: **active** — it connects to each target port and sends probe bytes, so
it's gated behind operator approval. Per the deepest probe tier chosen for
Phase 1, each port gets: a banner read (with a newline nudge for services that
stay quiet until spoken to), an HTTP request, and a TLS handshake to pull
certificate details. Pure-Python — asyncio + stdlib ssl + httpx + cryptography,
no nmap.

``target`` is an IP (the dispatch node resolves hostnames before the gate).
``ports`` may be a list/string of ports to fingerprint; omit to probe a small
common set — normally you pass the open ports a prior portscan/subnet_sweep
turned up.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import ssl
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from cryptography import x509
from cryptography.x509.oid import NameOID

from app.orchestrator.tools.portscan import _SERVICE_NAMES, _parse_ports, port_severity
from app.orchestrator.tools.runtime import ToolResult

CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 3.0
HTTP_TIMEOUT_S = 6.0
BANNER_BYTES = 2048
PROBE_CONCURRENCY = 20
MAX_PORTS = 100  # fingerprinting is far heavier per port than a connect scan

# Ports fingerprinted when the caller passes none. Normally the agent supplies
# the open ports a prior scan found; this is a sane fallback.
DEFAULT_PORTS: tuple[int, ...] = (
    21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 3306, 3389, 5432, 6379, 8080, 8443,
)
_TLS_HINT_PORTS = {443, 465, 636, 993, 995, 8443, 9443}

_SSH_RE = re.compile(r"SSH-\d+\.\d+-(\S+)")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_NONPRINTABLE_RE = re.compile(r"[^\x20-\x7e]+")


def _clean(data: bytes, limit: int = 300) -> str:
    text = _NONPRINTABLE_RE.sub(" ", data.decode("latin-1", errors="replace")).strip()
    return text[:limit]


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError, TimeoutError):
        await asyncio.wait_for(writer.wait_closed(), 1.0)


async def _read_some(reader: asyncio.StreamReader) -> bytes:
    try:
        return await asyncio.wait_for(reader.read(BANNER_BYTES), READ_TIMEOUT_S)
    except (OSError, TimeoutError):
        return b""


async def _grab_banner(ip: str, port: int) -> str | None:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), CONNECT_TIMEOUT_S
        )
    except (OSError, TimeoutError):
        return None
    try:
        data = await _read_some(reader)
        if not data:
            # Nudge a service that volunteers nothing on connect.
            with contextlib.suppress(OSError):
                writer.write(b"\r\n")
                await writer.drain()
            data = await _read_some(reader)
        return _clean(data) or None
    finally:
        await _close(writer)


def _name_cn(name: x509.Name) -> str | None:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else None


def _parse_cert(der: bytes) -> dict[str, Any]:
    try:
        cert = x509.load_der_x509_certificate(der)
    except ValueError:
        return {}
    info: dict[str, Any] = {}
    if cn := _name_cn(cert.subject):
        info["subject_cn"] = cn
    if issuer := _name_cn(cert.issuer):
        info["issuer_cn"] = issuer
    with contextlib.suppress(Exception):
        info["not_after"] = cert.not_valid_after_utc.isoformat()
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = san.value.get_values_for_type(x509.DNSName)
        if names:
            info["sans"] = names[:20]
    except x509.ExtensionNotFound:
        pass
    return info


async def _tls_info(ip: str, port: int, sni: str | None) -> dict[str, Any] | None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port, ssl=ctx, server_hostname=sni),
            CONNECT_TIMEOUT_S,
        )
    except (OSError, TimeoutError, ssl.SSLError):
        return None
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        if ssl_obj is None:
            return None
        info: dict[str, Any] = {"version": ssl_obj.version()}
        der = ssl_obj.getpeercert(binary_form=True)
        if der:
            info.update(_parse_cert(der))
        return info
    finally:
        await _close(writer)


async def _http_info(ip: str, port: int, *, tls: bool) -> dict[str, Any] | None:
    url = f"{'https' if tls else 'http'}://{ip}:{port}/"
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_S,
            verify=False,
            follow_redirects=False,
            headers={"User-Agent": "redteam-dashboard/0.0.1 (+phase1)"},
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return None
    info: dict[str, Any] = {
        "status": resp.status_code,
        "server": resp.headers.get("server"),
        "x_powered_by": resp.headers.get("x-powered-by"),
    }
    if "text/html" in resp.headers.get("content-type", "").lower():
        match = _TITLE_RE.search(resp.text[:100_000])
        if match:
            info["title"] = " ".join(match.group(1).split())[:200]
    return info


_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _severity_for_probe(port: int, probe: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (severity, signals) for a per-port probe.

    Baseline comes from ``port_severity`` (operator priority by port). Concrete
    service signals bump it: a Redis that responds without auth is the canonical
    "DB exposed" find, an anonymous FTP banner is its file-share twin, and a
    cert past its ``not_after`` is a hard finding regardless of what's behind it.
    """
    base = port_severity(port)
    bumps: list[tuple[str, str]] = []

    banner = (probe.get("banner") or "").lower()
    service = (probe.get("service") or "").lower()

    # Bump only on positive evidence in the banner: NOAUTH tells us auth is
    # required (still exposed, but gated), +PONG / redis_version tells us the
    # server is answering without any auth at all — the canonical exposed-DB
    # finding. Without a banner we leave the bare port-based severity alone.
    if "noauth" in banner:
        bumps.append(("high", "redis exposed (auth required)"))
    elif "redis_version" in banner or "+pong" in banner:
        bumps.append(("critical", "redis responding without authentication"))

    if service == "ftp" and ("anonymous" in banner or "anon ftp" in banner):
        bumps.append(("high", "anonymous FTP advertised"))

    tls = probe.get("tls") or {}
    not_after = tls.get("not_after")
    if not_after:
        try:
            expiry = datetime.fromisoformat(str(not_after))
        except ValueError:
            expiry = None
        if expiry is not None:
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            now = datetime.now(tz=UTC)
            if expiry < now:
                bumps.append(("high", f"TLS cert expired ({not_after})"))
            elif (expiry - now).days < 30:
                bumps.append(("medium", f"TLS cert expires within 30 days ({not_after})"))

    if not bumps:
        return base, []
    best = max(
        [base, *(level for level, _ in bumps)],
        key=lambda s: _SEVERITY_RANK[s],
    )
    return best, [reason for _, reason in bumps]


def _identify(
    port: int, banner: str | None, http: dict[str, Any] | None, tls: dict[str, Any] | None
) -> tuple[str | None, str | None]:
    b = banner or ""
    if m := _SSH_RE.search(b):
        return "ssh", m.group(1)
    upper = b.upper()
    if b.startswith("220") and "FTP" in upper:
        return "ftp", b[:120]
    if b.startswith("220") and ("SMTP" in upper or "ESMTP" in upper):
        return "smtp", b[:120]
    if "redis" in b.lower() or b.startswith(("-NOAUTH", "-ERR", "+PONG")):
        return "redis", None
    if http:
        return ("https" if tls else "http"), http.get("server")
    if tls:
        return "tls", None
    return _SERVICE_NAMES.get(port), None


async def _probe_port(ip: str, port: int, sni: str | None) -> dict[str, Any]:
    banner = await _grab_banner(ip, port)
    tls = await _tls_info(ip, port, sni)
    http = await _http_info(ip, port, tls=bool(tls))
    service, product = _identify(port, banner, http, tls)

    entry: dict[str, Any] = {"port": port, "service": service}
    if product:
        entry["product"] = product
    if banner:
        entry["banner"] = banner
    if tls:
        entry["tls"] = tls
    if http:
        entry["http"] = http
    return entry


async def _scan(ip: str, ports: list[int], sni: str | None) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(port: int) -> dict[str, Any]:
        async with sem:
            return await _probe_port(ip, port, sni)

    return list(await asyncio.gather(*(one(p) for p in ports)))


def service_detect_impl(args: Mapping[str, Any]) -> ToolResult:
    target = str(args.get("target") or "").strip()
    if not target:
        return ToolResult(ok=False, error="missing or empty 'target' arg")
    try:
        ip = str(ipaddress.ip_address(target))
    except ValueError:
        return ToolResult(
            ok=False,
            error=f"target {target!r} is not an IP address (resolve it first)",
        )

    raw_ports = args.get("ports")
    try:
        ports = _parse_ports(raw_ports) if raw_ports else list(DEFAULT_PORTS)
    except (ValueError, TypeError) as exc:
        return ToolResult(ok=False, error=f"bad 'ports' arg: {exc}")
    if not ports:
        return ToolResult(ok=False, error="no valid ports to probe")
    if len(ports) > MAX_PORTS:
        return ToolResult(
            ok=False,
            error=f"too many ports ({len(ports)} > {MAX_PORTS}); narrow to the open ones",
        )

    sni = args.get("resolved_from") or None
    services = asyncio.run(_scan(ip, ports, sni))

    data: dict[str, Any] = {
        "target": ip,
        "ports_probed": len(ports),
        "services": services,
    }
    if args.get("resolved_from"):
        data["resolved_from"] = args["resolved_from"]

    # Emit one finding per port that produced any signal — a banner / TLS / HTTP
    # response, or an identified service by port (e.g. redis on 6379 that said
    # nothing to our generic CRLF nudge but is still an open DB). Silent +
    # unidentified ports stay in ``data.services`` for the LLM but don't
    # pollute the findings table.
    findings: list[dict[str, Any]] = []
    for probe in services:
        if not (
            probe.get("banner")
            or probe.get("tls")
            or probe.get("http")
            or probe.get("service")
        ):
            continue
        port = int(probe["port"])
        severity, signals = _severity_for_probe(port, probe)
        service_name = probe.get("service") or "unknown"
        product = probe.get("product")
        title = f"{service_name} on {ip}:{port}"
        if product:
            title = f"{title} — {product}"
        finding_data = dict(probe)
        if signals:
            finding_data["signals"] = signals
        if args.get("resolved_from"):
            finding_data["resolved_from"] = args["resolved_from"]
        findings.append(
            {
                "target": f"{ip}:{port}",
                "severity": severity,
                "title": title,
                "data": finding_data,
            }
        )

    return ToolResult(ok=True, data=data, findings=findings)
