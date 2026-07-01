"""Auto-detect manifest fields from an uploaded Python source (v0.12.0).

Three tiers of inference, tried in order:

1. **Module-level ``TOOL`` dict.** The primary "just write Python"
   pattern. Backend AST-parses the source, finds a top-level
   ``TOOL = {...}`` assignment, and reads it with :func:`ast.literal_eval`.
   Zero imports required at the tool side — the dict is a plain Python
   literal.

2. **Module docstring.** Used as ``description`` if the manifest
   doesn't declare one.

3. **Filename.** Used as ``entrypoint`` (always) and, as a last-ditch
   fallback, as the tool name (kebab-cased).

Everything the parser CAN infer is returned as a partial manifest; the
frontend renders what's missing as a small form. Nothing is enforced
here — validation runs later via
:mod:`app.services.tool_manifest.parse_manifest` on the final YAML.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

_KEBAB_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class InferredManifest:
    """Partial manifest built from an uploaded source. ``fields`` maps
    manifest.spec.<key> → value. ``missing`` lists fields the analyst
    still needs to fill (required + not inferred)."""

    name: str | None = None
    description: str | None = None
    entrypoint: str = "main.py"
    kind: str = "python"
    lane: str = "analyst"
    fields: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_REQUIRED_MANIFEST_FIELDS = (
    "risk_level",
    "task_kind",
)


def infer_from_python_source(
    source: str, filename: str = "main.py"
) -> InferredManifest:
    """Read as many manifest fields as possible from a Python source
    string. Never raises for parse errors — the frontend surfaces those
    via the validation pass later; here we just return whatever landed.
    """
    inferred = InferredManifest(entrypoint=filename)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        inferred.warnings.append(f"syntax error at line {exc.lineno}: {exc.msg}")
        return inferred

    # (2) Module docstring → default description
    docstring = ast.get_docstring(tree)
    if docstring:
        inferred.description = docstring.strip().split("\n\n", 1)[0]

    # (1) Module-level TOOL = {...}
    tool_dict = _find_tool_dict(tree)
    if tool_dict is not None:
        inferred.name = tool_dict.get("name") or inferred.name
        inferred.description = (
            tool_dict.get("description") or inferred.description
        )
        for key in (
            "risk_level",
            "task_kind",
            "network_egress",
            "python_deps",
            "args",
            "timeout_seconds",
        ):
            if key in tool_dict:
                inferred.fields[key] = tool_dict[key]
        # Allow overriding kind / lane / entrypoint too, though 99% of
        # the time the defaults (python / analyst / main.py) are right.
        for key in ("kind", "lane", "entrypoint"):
            if key in tool_dict:
                setattr(inferred, key, tool_dict[key])

    # (3) Fallback name from filename if nothing else set it
    if not inferred.name:
        stem = filename.rsplit(".", 1)[0]
        inferred.name = _kebabify(stem) or "unnamed-tool"

    # Compute what the analyst still needs to fill
    for req in _REQUIRED_MANIFEST_FIELDS:
        if req not in inferred.fields:
            inferred.missing.append(f"spec.{req}")

    return inferred


def _find_tool_dict(tree: ast.Module) -> dict[str, Any] | None:
    """Look for ``TOOL = {...}`` at module scope. Uses
    :func:`ast.literal_eval` so anything more complex than plain literals
    is rejected — good, means the tool author can't sneak an import into
    "metadata"."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != "TOOL":
            continue
        try:
            return ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return None
    return None


def _kebabify(s: str) -> str:
    return _KEBAB_RE.sub("-", s.lower()).strip("-")


def inferred_to_manifest_yaml(inferred: InferredManifest) -> str:
    """Turn an :class:`InferredManifest` back into YAML text so the
    upload wizard's "YAML" tab can preview what was extracted, or the
    frontend can hand a merged form+inferred payload back to the same
    :mod:`tool_manifest` code path."""
    import yaml

    doc: dict[str, Any] = {
        "apiVersion": "rtd.tools/v1",
        "kind": "Tool",
        "metadata": {
            "name": inferred.name,
            **({"description": inferred.description} if inferred.description else {}),
        },
        "spec": {
            "kind": inferred.kind,
            "lane": inferred.lane,
            "entrypoint": inferred.entrypoint,
            **inferred.fields,
        },
    }
    return yaml.safe_dump(doc, sort_keys=False)
