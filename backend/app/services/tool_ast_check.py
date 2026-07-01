"""Python source AST allow-list check (v0.11.0, layer 1 of the analyst
lane gate).

Parses the uploaded Python source and rejects any import that isn't on
the explicit allow-list. This is *not* a sandbox — it's a hygiene check
that catches obvious footguns before they hit the admin approval queue.
The load-bearing runtime isolation ships in v0.12.0 (sibling container
per invocation).

Allow-list philosophy: what an enumeration / scanning tool plausibly
needs. HTTP clients, DNS, socket read ops, JSON, structured data. What
it does *not* need: subprocess execution, ctypes, pty, raw filesystem
walks outside the tool's own dir, dynamic imports of untrusted modules.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

_ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        # stdlib safe subset
        "base64",
        "collections",
        "contextlib",
        "csv",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "hmac",
        "io",
        "ipaddress",
        "itertools",
        "json",
        "logging",
        "math",
        "pathlib",
        "random",
        "re",
        "socket",
        "ssl",
        "string",
        "struct",
        "sys",
        "textwrap",
        "time",
        "typing",
        "urllib.parse",
        "uuid",
        # third-party network + parsing (present in backend deps)
        "httpx",
        "requests",
        "dns",
        "dns.resolver",
        "dns.reversename",
        "yaml",
        "defusedxml",
        "defusedxml.ElementTree",
    }
)

# v0.15.1 fix: map third-party import names to the pip package that
# provides them. Used by ``infer_python_deps`` to auto-populate the
# manifest's ``python_deps`` from imports the analyst forgot to
# declare — the sandbox base image is ``python:3.12-slim`` and won't
# have ``httpx`` etc. unless we pip-install at container start.
#
# Only include third-party imports that are already on the allow-list
# above — nothing that would have been rejected at the AST layer can
# sneak into python_deps via this map.
_IMPORT_TO_PIP: dict[str, str] = {
    "httpx": "httpx",
    "requests": "requests",
    "dns": "dnspython",
    "dns.resolver": "dnspython",
    "dns.reversename": "dnspython",
    "yaml": "PyYAML",
    "defusedxml": "defusedxml",
    "defusedxml.ElementTree": "defusedxml",
}


def infer_python_deps(imports_seen: list[str]) -> list[str]:
    """Return the pip package names that the given imports need at
    runtime, deduped and sorted. stdlib imports return the empty list.
    Unknown third-party imports (which the AST check would have already
    rejected) also return the empty list — belt and suspenders."""
    pkgs: set[str] = set()
    for imp in imports_seen:
        if imp in _IMPORT_TO_PIP:
            pkgs.add(_IMPORT_TO_PIP[imp])
    return sorted(pkgs)

_BANNED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        # os module — allow the module for path work but ban shellouts
        "os.system",
        "os.popen",
        "os.execv",
        "os.execvp",
        "os.execve",
        "os.execvpe",
        "os.spawnl",
        "os.spawnv",
        "os.spawnvp",
        "os.spawnve",
        "os.fork",
        "os.forkpty",
        # subprocess is fully banned — invocation runs already in a
        # sandboxed container; a tool spawning children inside that
        # container is out of scope for v0.11.
        "subprocess.run",
        "subprocess.call",
        "subprocess.Popen",
        "subprocess.check_call",
        "subprocess.check_output",
        # eval / exec at the module level are always suspicious
        "builtins.exec",
        "builtins.eval",
        "builtins.compile",
        # ctypes = binary interop = out of scope for a Python tool
        "ctypes.CDLL",
    }
)

_BANNED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "subprocess",
        "ctypes",
        "pty",
        "multiprocessing",
        "asyncio.subprocess",
    }
)


@dataclass
class ASTCheckResult:
    ok: bool
    imports_seen: list[str] = field(default_factory=list)
    disallowed_imports: list[str] = field(default_factory=list)
    banned_calls: list[str] = field(default_factory=list)
    syntax_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "imports_seen": sorted(self.imports_seen),
            "disallowed_imports": sorted(self.disallowed_imports),
            "banned_calls": sorted(self.banned_calls),
            "syntax_error": self.syntax_error,
        }


def check_python_source(source: str) -> ASTCheckResult:
    """Parse ``source`` and return a structured verdict.

    ``ok=True`` only when: (1) source parses, (2) every import root or
    dotted path is on the allow-list, (3) no banned attribute is
    referenced (via ``os.system``-style access or ``from subprocess
    import X`` sneak-arounds).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ASTCheckResult(ok=False, syntax_error=str(exc))

    imports_seen: set[str] = set()
    disallowed: set[str] = set()
    banned_calls: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports_seen.add(alias.name)
                if not _import_allowed(alias.name):
                    disallowed.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports_seen.add(module)
            if not _import_allowed(module):
                disallowed.add(module)
            # `from subprocess import Popen` — the top-level module is
            # what matters; catch it even if someone renames on import.
            if module in _BANNED_TOP_LEVEL:
                banned_calls.add(f"from {module} import ...")
        elif isinstance(node, ast.Attribute):
            dotted = _dotted_name(node)
            if dotted and dotted in _BANNED_ATTRIBUTES:
                banned_calls.add(dotted)
        elif isinstance(node, ast.Call):
            # exec()/eval()/compile() as bare calls (not attribute access)
            func = node.func
            if isinstance(func, ast.Name) and func.id in {
                "exec",
                "eval",
                "compile",
            }:
                banned_calls.add(f"builtins.{func.id}")

    ok = not disallowed and not banned_calls
    return ASTCheckResult(
        ok=ok,
        imports_seen=sorted(imports_seen),
        disallowed_imports=sorted(disallowed),
        banned_calls=sorted(banned_calls),
    )


def _import_allowed(module: str) -> bool:
    """Match either the full dotted name or the top-level root against
    the allow-list. ``dns.resolver`` matches because the exact string is
    listed; ``os.path`` doesn't (deliberately — ``os`` is not on the
    list, and the banned-attribute layer catches specific footguns)."""
    if not module:
        return False
    if module in _ALLOWED_IMPORTS:
        return True
    # allow_list also lets a submodule through if the root is on it —
    # e.g. `urllib.parse` allows `urllib.parse.quote`. Not the other
    # way around: allowing `dns.resolver` does NOT allow `dns`.
    root = module.split(".", 1)[0]
    if root in _ALLOWED_IMPORTS:
        # Only allow the root itself, not arbitrary submodules under it
        return module == root
    return False


def _dotted_name(node: ast.Attribute) -> str | None:
    """Reconstruct ``os.system`` / ``subprocess.Popen`` style attribute
    chains. Returns None if the chain has a non-Name / non-Attribute
    node in it (e.g. ``foo().system``)."""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None
