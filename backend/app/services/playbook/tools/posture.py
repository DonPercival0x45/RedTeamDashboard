"""Keyless, bounded posture checks for passive playbook recipes.

These checks collect DNS or one HTTP response. They never claim resources,
brute-force selectors, crawl paths, follow redirects, or mutate remote systems.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

import dns.exception
import dns.resolver
import httpx

from app.services.playbook.executor import StepResult

_RESOLVER_TIMEOUT = 3.0
_RESOLVER_LIFETIME = 5.0
_HTTP_TIMEOUT = 10.0
_HEADER_ALLOWLIST = {
    "access-control-allow-origin",
    "cache-control",
    "content-security-policy",
    "cross-origin-embedder-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
    "location",
    "permissions-policy",
    "referrer-policy",
    "server",
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "x-powered-by",
}
_EDGE_SUFFIXES = {
    "akamai.net": "Akamai",
    "akamaiedge.net": "Akamai",
    "azureedge.net": "Microsoft Azure CDN",
    "barracudanetworks.com": "Barracuda",
    "cloudflare.net": "Cloudflare",
    "cloudfront.net": "Amazon CloudFront",
    "fastly.net": "Fastly",
}


def _resolver() -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver()
    resolver.timeout = _RESOLVER_TIMEOUT
    resolver.lifetime = _RESOLVER_LIFETIME
    return resolver


def _query(name: str, record_type: str) -> tuple[list[str], str | None]:
    try:
        answers = _resolver().resolve(name, record_type)
        return [str(answer).strip().rstrip(".") for answer in answers], None
    except dns.resolver.NXDOMAIN:
        return [], "nxdomain"
    except dns.resolver.NoAnswer:
        return [], "no_answer"
    except dns.resolver.NoNameservers:
        return [], "no_nameservers"
    except (dns.exception.Timeout, dns.resolver.LifetimeTimeout):
        return [], "timeout"
    except Exception as exc:  # pragma: no cover - resolver backend variance
        return [], f"dns_error:{type(exc).__name__}"


def _target_domain(scope_context: str, args: dict[str, Any]) -> str:
    return str(args.get("domain") or scope_context).strip().lower().rstrip(".")


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "info",
    confidence: str = "high",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "severity": severity,
        "confidence": confidence,
        "evidence": evidence or {},
    }


def _http_url(scope_context: str, args: dict[str, Any]) -> str:
    candidate = str(args.get("url") or args.get("domain") or scope_context).strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    if parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("target must be a hostname or HTTP(S) URL without credentials")
    return candidate


def _http_snapshot(scope_context: str, args: dict[str, Any]) -> dict[str, Any]:
    url = _http_url(scope_context, args)
    hostname = urlsplit(url).hostname or ""
    try:
        resolved = {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise ValueError(f"target hostname did not resolve safely: {exc}") from exc
    if not resolved or any(not address.is_global for address in resolved):
        raise ValueError("HTTP posture checks require globally routable target addresses")
    headers: dict[str, str] = {}
    cookies: list[dict[str, Any]] = []
    with (
        httpx.Client(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "RTD-Keyless-Posture/1.0"},
        ) as client,
        client.stream("GET", url) as response,
    ):
        headers = {
            key.lower(): value
            for key, value in response.headers.items()
            if key.lower() in _HEADER_ALLOWLIST
        }
        for raw_cookie in response.headers.get_list("set-cookie")[:50]:
            lowered = raw_cookie.lower()
            cookies.append(
                {
                    "name": raw_cookie.split("=", 1)[0].strip(),
                    "secure": "; secure" in lowered,
                    "http_only": "; httponly" in lowered,
                    "same_site": next(
                        (
                            part.split("=", 1)[1]
                            for part in raw_cookie.split(";")
                            if part.strip().lower().startswith("samesite=")
                        ),
                        None,
                    ),
                }
            )
        return {
            "url": url,
            "status": response.status_code,
            "headers": headers,
            "cookies": cookies,
            "redirect": headers.get("location"),
        }


def run_dns_ownership_boundary(scope_context: str, args: dict[str, Any]) -> StepResult:
    domain = _target_domain(scope_context, args)
    if not domain:
        return StepResult(ok=False, error="domain is required")
    records: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for record_type in ("NS", "SOA", "MX", "CNAME", "A", "AAAA"):
        values, error = _query(domain, record_type)
        records[record_type.lower()] = values
        if error and error != "no_answer":
            errors[record_type.lower()] = error
    issues: list[dict[str, Any]] = []
    external_ns = [
        host
        for host in records["ns"]
        if host.lower() != domain and not host.lower().endswith(f".{domain}")
    ]
    if external_ns:
        issues.append(
            _issue(
                "external_dns_dependency",
                "Authoritative DNS is delegated outside the reviewed domain boundary",
                evidence={"nameservers": external_ns},
            )
        )
    external_mx = []
    for value in records["mx"]:
        host = value.split()[-1].lower() if value.split() else ""
        if host and host != domain and not host.endswith(f".{domain}"):
            external_mx.append(host)
    if external_mx:
        issues.append(
            _issue(
                "external_mail_dependency",
                "Mail exchange is hosted outside the reviewed domain boundary",
                evidence={"mail_exchanges": external_mx},
            )
        )
    data = {
        "check": "dns_ownership_boundary",
        "domain": domain,
        "records": records,
        "issues": issues,
        "observations": [{"code": "dns_query_errors", "errors": errors}] if errors else [],
    }
    return StepResult(ok=True, data=data, findings_total=max(1, len(issues)))


def run_mail_auth_posture(scope_context: str, args: dict[str, Any]) -> StepResult:
    domain = _target_domain(scope_context, args)
    if not domain:
        return StepResult(ok=False, error="domain is required")
    mx, mx_error = _query(domain, "MX")
    apex_txt, txt_error = _query(domain, "TXT")
    dmarc_txt, dmarc_error = _query(f"_dmarc.{domain}", "TXT")
    mta_sts_txt, mta_error = _query(f"_mta-sts.{domain}", "TXT")
    tls_rpt_txt, tls_error = _query(f"_smtp._tls.{domain}", "TXT")
    spf = [value.strip('"') for value in apex_txt if "v=spf1" in value.lower()]
    dmarc = [value.strip('"') for value in dmarc_txt if "v=dmarc1" in value.lower()]
    mta_sts = [value.strip('"') for value in mta_sts_txt if "v=stsv1" in value.lower()]
    tls_rpt = [value.strip('"') for value in tls_rpt_txt if "v=tlsrptv1" in value.lower()]
    issues: list[dict[str, Any]] = []
    if not mx:
        issues.append(_issue("mx_missing", "No MX record was observed", severity="low"))
    if not spf:
        issues.append(_issue("spf_missing", "No SPF record was observed", severity="low"))
    elif len(spf) > 1:
        issues.append(
            _issue(
                "spf_multiple",
                "Multiple SPF records were observed",
                severity="medium",
                evidence={"records": spf},
            )
        )
    if not dmarc:
        issues.append(_issue("dmarc_missing", "No DMARC record was observed", severity="medium"))
    elif len(dmarc) > 1:
        issues.append(
            _issue(
                "dmarc_multiple",
                "Multiple DMARC records were observed",
                severity="medium",
                evidence={"records": dmarc},
            )
        )
    elif "p=none" in dmarc[0].replace(" ", "").lower():
        issues.append(
            _issue(
                "dmarc_monitoring_only",
                "DMARC policy is monitoring-only (p=none)",
                severity="low",
                evidence={"record": dmarc[0]},
            )
        )
    if not mta_sts:
        issues.append(_issue("mta_sts_missing", "No MTA-STS TXT record was observed"))
    if not tls_rpt:
        issues.append(_issue("tls_rpt_missing", "No SMTP TLS reporting record was observed"))
    query_errors = {
        key: value
        for key, value in {
            "mx": mx_error,
            "txt": txt_error,
            "dmarc": dmarc_error,
            "mta_sts": mta_error,
            "tls_rpt": tls_error,
        }.items()
        if value and value not in {"no_answer", "nxdomain"}
    }
    data = {
        "check": "mail_auth_posture",
        "domain": domain,
        "mx": mx,
        "spf": spf,
        "dmarc": dmarc,
        "mta_sts": mta_sts,
        "tls_rpt": tls_rpt,
        "dkim": {
            "status": "not_tested",
            "reason": "Selector discovery is not exhaustive without configured selectors",
        },
        "issues": issues,
        "observations": [{"code": "dns_query_errors", "errors": query_errors}]
        if query_errors
        else [],
    }
    return StepResult(ok=True, data=data, findings_total=max(1, len(issues)))


def run_dangling_dns_triage(scope_context: str, args: dict[str, Any]) -> StepResult:
    domain = _target_domain(scope_context, args)
    if not domain:
        return StepResult(ok=False, error="domain is required")
    cname, cname_error = _query(domain, "CNAME")
    issues: list[dict[str, Any]] = []
    terminal: dict[str, Any] = {"state": "no_cname", "addresses": []}
    if cname:
        target = cname[0].lower()
        a, a_error = _query(target, "A")
        aaaa, aaaa_error = _query(target, "AAAA")
        errors = {error for error in (a_error, aaaa_error) if error}
        state = "resolved" if a or aaaa else "inconclusive"
        if errors == {"nxdomain"}:
            state = "nxdomain"
            issues.append(
                _issue(
                    "dangling_cname_candidate",
                    "CNAME target returned NXDOMAIN; manual provider-specific "
                    "validation is required",
                    severity="medium",
                    confidence="medium",
                    evidence={"cname": target},
                )
            )
        terminal = {
            "state": state,
            "target": target,
            "addresses": [*a, *aaaa],
            "errors": sorted(errors),
        }
    elif cname_error not in {None, "no_answer", "nxdomain"}:
        terminal = {"state": "inconclusive", "addresses": [], "errors": [cname_error]}
    data = {
        "check": "dangling_dns_triage",
        "domain": domain,
        "cname_chain": cname[:1],
        "terminal": terminal,
        "issues": issues,
        "observations": [],
    }
    return StepResult(ok=True, data=data, findings_total=max(1, len(issues)))


def run_web_security_baseline(scope_context: str, args: dict[str, Any]) -> StepResult:
    try:
        snapshot = _http_snapshot(scope_context, args)
    except (httpx.HTTPError, ValueError) as exc:
        return StepResult(ok=False, error=f"HTTP baseline failed: {exc}")
    headers = snapshot["headers"]
    issues: list[dict[str, Any]] = []
    if snapshot["url"].startswith("https://") and "strict-transport-security" not in headers:
        issues.append(_issue("hsts_missing", "HTTPS response did not include HSTS", severity="low"))
    for header, code, label in (
        ("content-security-policy", "csp_missing", "Content-Security-Policy"),
        ("x-content-type-options", "nosniff_missing", "X-Content-Type-Options"),
        ("referrer-policy", "referrer_policy_missing", "Referrer-Policy"),
    ):
        if header not in headers:
            issues.append(_issue(code, f"Response did not include {label}", severity="low"))
    insecure_cookies = [
        cookie for cookie in snapshot["cookies"] if not cookie["secure"] or not cookie["http_only"]
    ]
    if insecure_cookies:
        issues.append(
            _issue(
                "cookie_flags_incomplete",
                "One or more response cookies lacked Secure or HttpOnly",
                severity="low",
                evidence={"cookies": insecure_cookies},
            )
        )
    domain = urlsplit(snapshot["url"]).hostname or scope_context
    data = {
        "check": "web_security_baseline",
        "domain": domain,
        **snapshot,
        "issues": issues,
        "observations": [],
    }
    return StepResult(ok=True, data=data, findings_total=max(1, len(issues)))


def run_cloud_edge_boundary(scope_context: str, args: dict[str, Any]) -> StepResult:
    domain = _target_domain(scope_context, args)
    if not domain:
        return StepResult(ok=False, error="domain is required")
    cname, cname_error = _query(domain, "CNAME")
    a, a_error = _query(domain, "A")
    aaaa, aaaa_error = _query(domain, "AAAA")
    edges: list[dict[str, Any]] = []
    for target in cname:
        lowered = target.lower()
        for suffix, provider in _EDGE_SUFFIXES.items():
            if lowered == suffix or lowered.endswith(f".{suffix}"):
                edges.append(
                    {"provider": provider, "signal": f"cname:{target}", "confidence": "high"}
                )
    snapshot: dict[str, Any] | None = None
    try:
        snapshot = _http_snapshot(scope_context, {"domain": domain})
    except (httpx.HTTPError, ValueError):
        snapshot = None
    if snapshot:
        server = str(snapshot["headers"].get("server") or "").lower()
        if "cloudflare" in server and not any(edge["provider"] == "Cloudflare" for edge in edges):
            edges.append(
                {"provider": "Cloudflare", "signal": f"server:{server}", "confidence": "medium"}
            )
    non_global = []
    for value in [*a, *aaaa]:
        try:
            if not ipaddress.ip_address(value).is_global:
                non_global.append(value)
        except ValueError:
            continue
    issues = (
        [
            _issue(
                "non_global_dns_address",
                "Public DNS returned a non-global address",
                severity="low",
                evidence={"addresses": non_global},
            )
        ]
        if non_global
        else []
    )
    errors = [
        error for error in (cname_error, a_error, aaaa_error) if error and error != "no_answer"
    ]
    data = {
        "check": "cloud_edge_boundary",
        "domain": domain,
        "dns": {"cname": cname, "a": a, "aaaa": aaaa, "errors": errors},
        "http": snapshot,
        "edges": edges,
        "origins": [],
        "issues": issues,
        "observations": [
            {
                "code": "edge_provider_signal",
                "message": "Provider signals describe delivery infrastructure, not asset ownership",
                "edges": edges,
            }
        ]
        if edges
        else [],
    }
    return StepResult(ok=True, data=data, findings_total=max(1, len(issues)))
