"""Unit tests for the real OSINT tool implementations.

External calls are mocked:
- httpx via respx
- dns.resolver.Resolver.resolve via monkeypatch
- whois.whois via monkeypatch

Lets the suite stay deterministic + offline-safe while still exercising the
parsing, normalization, and error paths in each impl.
"""
from __future__ import annotations

from typing import Any

import dns.resolver
import httpx
import pytest
import respx

from app.orchestrator.tools.crt_sh import crt_sh_impl
from app.orchestrator.tools.dns_lookup import dns_lookup_impl
from app.orchestrator.tools.httpx_probe import httpx_probe_impl
from app.orchestrator.tools.portscan import _parse_ports, port_severity, portscan_impl
from app.orchestrator.tools.reverse_dns import reverse_dns_impl
from app.orchestrator.tools.service_detect import service_detect_impl
from app.orchestrator.tools.subfinder import subfinder_impl
from app.orchestrator.tools.subnet_sweep import subnet_sweep_impl
from app.orchestrator.tools.whois_lookup import whois_lookup_impl

# ---------------------------------------------------------------------------
# crt.sh + subfinder
# ---------------------------------------------------------------------------


@respx.mock
def test_crt_sh_extracts_unique_in_scope_subdomains() -> None:
    respx.get("https://crt.sh/").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "common_name": "acme.com",
                    "name_value": "acme.com\nwww.acme.com\nmail.acme.com",
                },
                {
                    "common_name": "api.acme.com",
                    "name_value": "api.acme.com\n*.acme.com",
                },
                {
                    "common_name": "evil.com",
                    "name_value": "evil.com",
                },
            ],
        )
    )
    result = crt_sh_impl({"domain": "acme.com"})
    assert result.ok
    # *.acme.com excluded (wildcard); evil.com excluded (not a subdomain).
    assert result.data["subdomains"] == [
        "acme.com",
        "api.acme.com",
        "mail.acme.com",
        "www.acme.com",
    ]
    assert result.data["count"] == 4


@respx.mock
def test_crt_sh_http_error() -> None:
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(500))
    result = crt_sh_impl({"domain": "acme.com"})
    assert not result.ok
    assert "crt.sh" in (result.error or "")


def test_crt_sh_missing_domain() -> None:
    result = crt_sh_impl({})
    assert not result.ok
    assert "domain" in (result.error or "").lower()


@respx.mock
def test_subfinder_repackages_crt_sh_result() -> None:
    respx.get("https://crt.sh/").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"common_name": "acme.com", "name_value": "acme.com\nwww.acme.com"}
            ],
        )
    )
    result = subfinder_impl({"domain": "acme.com"})
    assert result.ok
    assert result.data["source"] == "crt.sh"
    assert set(result.data["subdomains"]) == {"acme.com", "www.acme.com"}


# ---------------------------------------------------------------------------
# dns_lookup
# ---------------------------------------------------------------------------


class _FakeAns:
    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:  # noqa: D401 — dunder; covered by behaviour
        return self._value


def _make_fake_resolve(
    answers: dict[str, list[str]],
) -> Any:
    def fake_resolve(self: Any, name: str, rtype: str) -> list[_FakeAns]:
        key = rtype.upper()
        if key not in answers:
            raise dns.resolver.NoAnswer()
        return [_FakeAns(v) for v in answers[key]]

    return fake_resolve


def test_dns_lookup_all_record_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dns.resolver.Resolver,
        "resolve",
        _make_fake_resolve(
            {
                "A": ["93.184.216.34"],
                "AAAA": ["2606:2800:220:1:248:1893:25c8:1946"],
                "MX": ["0 ."],
                "NS": ["a.iana-servers.net.", "b.iana-servers.net."],
                "TXT": ['"v=spf1 -all"'],
            }
        ),
    )
    result = dns_lookup_impl({"domain": "example.com"})
    assert result.ok
    assert result.data["a"] == ["93.184.216.34"]
    assert result.data["aaaa"] == ["2606:2800:220:1:248:1893:25c8:1946"]
    assert result.data["cname"] == []  # NoAnswer -> empty
    assert result.data["ns"] == ["a.iana-servers.net", "b.iana-servers.net"]


def test_dns_lookup_missing_domain() -> None:
    result = dns_lookup_impl({})
    assert not result.ok


def test_dns_lookup_handles_all_no_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dns.resolver.Resolver, "resolve", _make_fake_resolve({})
    )
    result = dns_lookup_impl({"domain": "no-records.example"})
    assert result.ok
    assert all(result.data[k] == [] for k in ("a", "aaaa", "cname", "mx", "ns", "txt"))


# ---------------------------------------------------------------------------
# reverse_dns
# ---------------------------------------------------------------------------


def test_reverse_dns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dns.resolver.Resolver,
        "resolve",
        _make_fake_resolve({"PTR": ["dns.google."]}),
    )
    result = reverse_dns_impl({"ip": "8.8.8.8"})
    assert result.ok
    assert result.data["ptr"] == ["dns.google"]


def test_reverse_dns_invalid_ip() -> None:
    result = reverse_dns_impl({"ip": "not-an-ip"})
    assert not result.ok
    assert "invalid ip" in (result.error or "")


# ---------------------------------------------------------------------------
# httpx_probe
# ---------------------------------------------------------------------------


@respx.mock
def test_httpx_probe_extracts_title_and_headers() -> None:
    respx.get("https://acme.com/").mock(
        return_value=httpx.Response(
            200,
            headers={
                "content-type": "text/html; charset=utf-8",
                "server": "nginx/1.25",
                "x-powered-by": "Express",
            },
            text="<html><head><title>  Acme  Welcome  </title></head></html>",
        )
    )
    result = httpx_probe_impl({"url": "https://acme.com/"})
    assert result.ok
    assert result.data["status"] == 200
    assert result.data["title"] == "Acme Welcome"
    assert result.data["server"] == "nginx/1.25"
    assert result.data["x_powered_by"] == "Express"


@respx.mock
def test_httpx_probe_normalizes_bare_host() -> None:
    respx.get("https://acme.com").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")
    )
    result = httpx_probe_impl({"url": "acme.com"})
    assert result.ok
    assert result.data["url"].startswith("https://")
    assert result.data["title"] is None  # not html


@respx.mock
def test_httpx_probe_error() -> None:
    respx.get("https://broken.example/").mock(side_effect=httpx.ConnectError("nope"))
    result = httpx_probe_impl({"url": "https://broken.example/"})
    assert not result.ok
    assert "probe failed" in (result.error or "")


# ---------------------------------------------------------------------------
# whois_lookup
# ---------------------------------------------------------------------------


class _FakeWhois:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def test_whois_serializes_mixed_types(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime

    fake = _FakeWhois(
        registrar="Example Registrar",
        creation_date=datetime(2010, 1, 2, 3, 4, 5),
        expiration_date=[datetime(2030, 1, 2), datetime(2031, 1, 2)],
        updated_date=None,
        name_servers=["a.iana-servers.net", "b.iana-servers.net"],
        status="ok",
        emails=["abuse@example.com"],
    )
    import whois as whois_mod

    monkeypatch.setattr(whois_mod, "whois", lambda _domain: fake)

    result = whois_lookup_impl({"domain": "example.com"})
    assert result.ok
    assert result.data["registrar"] == "Example Registrar"
    assert result.data["creation_date"] == "2010-01-02T03:04:05"
    assert result.data["expiration_date"] == [
        "2030-01-02T00:00:00",
        "2031-01-02T00:00:00",
    ]
    assert result.data["updated_date"] is None
    assert result.data["name_servers"] == [
        "a.iana-servers.net",
        "b.iana-servers.net",
    ]


def test_whois_missing_domain() -> None:
    result = whois_lookup_impl({})
    assert not result.ok


# ---------------------------------------------------------------------------
# portscan (active) — loopback only, no external network
# ---------------------------------------------------------------------------


def test_portscan_parse_ports() -> None:
    assert _parse_ports("22,80, 443") == [22, 80, 443]
    assert _parse_ports("8000-8002") == [8000, 8001, 8002]
    assert _parse_ports([22, "443", 22]) == [22, 443]
    # Out-of-range ports are dropped; reversed ranges normalize.
    assert _parse_ports("0,70000,443") == [443]
    assert _parse_ports("25-22") == [22, 23, 24, 25]


def test_portscan_detects_open_and_closed_ports() -> None:
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    open_port = listener.getsockname()[1]

    # Grab a port, then release it so (very likely) nothing is listening on it.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()

    try:
        result = portscan_impl(
            {"target": "127.0.0.1", "ports": [open_port, closed_port]}
        )
    finally:
        listener.close()

    assert result.ok, result.error
    assert result.data["open_ports"] == [open_port]
    assert result.data["open_count"] == 1
    assert result.data["ports_scanned"] == 2
    assert result.data["target"] == "127.0.0.1"


def test_portscan_rejects_non_ip_target() -> None:
    result = portscan_impl({"target": "example.com"})
    assert not result.ok
    assert "not an IP" in (result.error or "")


def test_portscan_missing_target() -> None:
    result = portscan_impl({})
    assert not result.ok
    assert "target" in (result.error or "")


def test_port_severity_buckets() -> None:
    # High-priority follow-ups: DBs + remote-access.
    assert port_severity(3389) == "high"  # RDP
    assert port_severity(6379) == "high"  # redis
    assert port_severity(3306) == "high"  # mysql
    # File-share / auth / lateral.
    assert port_severity(445) == "medium"  # smb
    assert port_severity(21) == "medium"  # ftp
    # Standard web/ssh.
    assert port_severity(22) == "low"
    assert port_severity(443) == "low"
    # Everything else.
    assert port_severity(12345) == "info"


def test_portscan_emits_per_port_findings_with_severity() -> None:
    import socket

    s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s1.bind(("127.0.0.1", 0))
    s1.listen(8)
    port_a = s1.getsockname()[1]
    s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s2.bind(("127.0.0.1", 0))
    s2.listen(8)
    port_b = s2.getsockname()[1]
    try:
        result = portscan_impl(
            {"target": "127.0.0.1", "ports": [port_a, port_b]}
        )
    finally:
        s1.close()
        s2.close()

    assert result.ok, result.error
    # Two open ports -> two findings, each scoped to that port.
    assert result.findings is not None
    assert len(result.findings) == 2
    assert {f["target"] for f in result.findings} == {
        f"127.0.0.1:{port_a}",
        f"127.0.0.1:{port_b}",
    }
    for f in result.findings:
        # Ephemeral ports land in the "info" bucket; the heuristic itself is
        # covered in test_port_severity_buckets.
        assert f["severity"] in {"info", "low", "medium", "high", "critical"}
        assert "open on 127.0.0.1:" in f["title"]
        assert f["data"]["host"] == "127.0.0.1"


# ---------------------------------------------------------------------------
# subnet_sweep (active) — loopback only (127.0.0.0/8 is all local on Linux)
# ---------------------------------------------------------------------------


def test_subnet_sweep_finds_live_host() -> None:
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    open_port = listener.getsockname()[1]

    try:
        # /30 covers 127.0.0.1 and 127.0.0.2; only .1 is listening.
        result = subnet_sweep_impl(
            {"cidr": "127.0.0.0/30", "ports": [open_port]}
        )
    finally:
        listener.close()

    assert result.ok, result.error
    assert result.data["hosts_scanned"] == 2
    assert result.data["live_host_count"] == 1
    live = result.data["live_hosts"][0]
    assert live["host"] == "127.0.0.1"
    assert live["open_ports"] == [open_port]


def test_subnet_sweep_skips_excluded_host() -> None:
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    open_port = listener.getsockname()[1]

    try:
        result = subnet_sweep_impl(
            {
                "cidr": "127.0.0.0/30",
                "ports": [open_port],
                "exclude": ["127.0.0.1"],
            }
        )
    finally:
        listener.close()

    assert result.ok, result.error
    assert result.data["hosts_excluded"] == 1
    assert result.data["hosts_scanned"] == 1
    # The only listening host was excluded, so nothing is reported live.
    assert result.data["live_host_count"] == 0


def test_subnet_sweep_rejects_oversize_range() -> None:
    result = subnet_sweep_impl({"cidr": "10.0.0.0/16"})
    assert not result.ok
    assert "too large" in (result.error or "")


def test_subnet_sweep_rejects_non_cidr() -> None:
    result = subnet_sweep_impl({"cidr": "not-a-cidr"})
    assert not result.ok
    assert "valid CIDR" in (result.error or "")


def test_subnet_sweep_emits_one_finding_per_host_port() -> None:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    open_port = s.getsockname()[1]
    try:
        result = subnet_sweep_impl(
            {"cidr": "127.0.0.0/30", "ports": [open_port]}
        )
    finally:
        s.close()

    assert result.ok, result.error
    assert result.findings is not None
    # Only 127.0.0.1 is live; one finding for that (host, port) pair.
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f["target"] == f"127.0.0.1:{open_port}"
    assert f["data"]["host"] == "127.0.0.1"
    assert f["data"]["port"] == open_port


# ---------------------------------------------------------------------------
# service_detect (active) — loopback servers, no external network
# ---------------------------------------------------------------------------


def test_service_detect_reads_banner() -> None:
    import contextlib
    import socket
    import threading

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    def serve() -> None:
        srv.settimeout(8)
        first = True
        for _ in range(6):
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            if first:
                with contextlib.suppress(OSError):
                    conn.sendall(b"SSH-2.0-OpenSSH_9.0p1 Ubuntu\r\n")
                first = False
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        result = service_detect_impl({"target": "127.0.0.1", "ports": [port]})
    finally:
        srv.close()

    assert result.ok, result.error
    svc = result.data["services"][0]
    assert svc["service"] == "ssh"
    assert "OpenSSH_9.0p1" in (svc.get("product") or "")


def test_service_detect_identifies_http() -> None:
    import http.server
    import socketserver
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"<html><head><title>Test Box</title></head><body>x</body></html>"
            self.send_response(200)
            self.send_header("Server", "TestServer/1.0")
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # noqa: ARG002
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        result = service_detect_impl({"target": "127.0.0.1", "ports": [port]})
    finally:
        srv.shutdown()
        srv.server_close()

    svc = result.data["services"][0]
    assert svc["service"] == "http"
    assert svc["http"]["status"] == 200
    # send_response() prepends its own Server header, so ours is appended.
    assert "TestServer/1.0" in svc["http"]["server"]
    assert svc["http"].get("title") == "Test Box"


def test_service_detect_parse_cert() -> None:
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from app.orchestrator.tools.service_detect import _parse_cert

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.local")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("test.local"), x509.DNSName("www.test.local")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    der = cert.public_bytes(serialization.Encoding.DER)

    info = _parse_cert(der)
    assert info["subject_cn"] == "test.local"
    assert info["issuer_cn"] == "test.local"
    assert "www.test.local" in info["sans"]
    assert "not_after" in info


def test_service_detect_rejects_non_ip() -> None:
    result = service_detect_impl({"target": "example.com"})
    assert not result.ok
    assert "not an IP" in (result.error or "")


def test_service_detect_missing_target() -> None:
    result = service_detect_impl({})
    assert not result.ok
    assert "target" in (result.error or "")


def test_service_detect_severity_bumps_on_signals() -> None:
    """Unit-level checks for the severity heuristic — concrete service signals
    bump above the bare port default, with reasons surfaced in ``signals``."""
    from datetime import UTC, datetime, timedelta

    from app.orchestrator.tools.service_detect import _severity_for_probe

    # Redis answering without auth (+PONG / redis_version in banner) -> critical
    sev, sig = _severity_for_probe(
        6379, {"port": 6379, "service": "redis", "banner": "+pong"}
    )
    assert sev == "critical"
    assert any("auth" in s.lower() for s in sig)

    # An expired TLS cert is "high" even on a low-baseline port like 443.
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    sev, sig = _severity_for_probe(
        443, {"port": 443, "service": "https", "tls": {"not_after": yesterday}}
    )
    assert sev == "high"
    assert any("expired" in s.lower() for s in sig)

    # Anonymous FTP advertised in the banner -> high.
    sev, sig = _severity_for_probe(
        21,
        {"port": 21, "service": "ftp", "banner": "220 anonymous ftp ready"},
    )
    assert sev == "high"

    # No bumps -> falls through to the port-based default.
    sev, sig = _severity_for_probe(
        22, {"port": 22, "service": "ssh", "banner": "SSH-2.0-OpenSSH"}
    )
    assert sev == "low"
    assert sig == []
