"""Deterministic recon primitives — Track A step A3b.

Each module here exposes a single ``run(scope_context, args) -> StepResult``
function. The ``InternalExecutor`` wires them into a slug dispatch table.
Plain sync IO: dnspython + python-whois today; httpx-based subfinder + crt.sh
+ breach lookup land as follow-ups once we've picked concrete data sources.

Playbook tools ≠ the analyst tool catalog (``app/models/tool.py``). Those are
user-uploaded Python packages running in a sandbox. These are first-party
recon primitives baked into the backend image — no sandbox, no user upload,
no per-invocation LLM. Deterministic scale means these run at 100k entities
by fan-out (A3c), not by AI cost.
"""
from app.services.playbook.tools.breach_lookup import (
    run as run_breach_lookup,
)
from app.services.playbook.tools.crtsh import run as run_crtsh
from app.services.playbook.tools.dns_inventory import run as run_dns_inventory
from app.services.playbook.tools.subfinder import run as run_subfinder
from app.services.playbook.tools.whois import run as run_whois

__all__ = [
    "run_breach_lookup",
    "run_crtsh",
    "run_dns_inventory",
    "run_subfinder",
    "run_whois",
]
