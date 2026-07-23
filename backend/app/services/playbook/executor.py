"""Playbook executor protocol + internal implementation.

The runner does NOT invoke tools directly. It calls
``PlaybookExecutor.run_step``, which is where "how the tool actually runs"
lives — internal (in-process) or MCP (out-of-process). Same signature, two
implementations; the runner is agnostic.

A3a ships:
* ``PlaybookExecutor`` — the ``Protocol`` interface.
* ``StepResult`` — the return-value dataclass every executor produces.
* ``InternalExecutor`` — the in-process impl. **A3a is the seam, not the wire
  to the tool registry** — the ``run_step`` implementation is a NotImplemented
  stub. Real wiring to ``services.tool_invocation`` lands in A3b. This lets us
  ship the runner + coverage-writing surface reviewable on its own and use a
  ``MockExecutor`` in tests to prove the seam.

A4 adds ``MCPExecutor`` implementing the same protocol, dispatched by
``PlaybookRun.disposition`` (once we thread work-item dispositions here).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class StepResult:
    """The runner-facing outcome of one tool invocation.

    Executors return this. The runner uses:

    * ``ok`` — did the tool succeed? Drives ``CoverageRecord.status`` +
      ``PlaybookRun.steps_succeeded/failed`` counters.
    * ``findings_*`` — accumulate into ``PlaybookRun`` and populate the
      ``FindingsSummary`` in the ``collection.job.completed`` milestone.
      Executors compute these deterministically (counts, not summaries).
    * ``error`` — populated iff ``ok is False``; short human-readable
      message. Stored on the coverage record's ``notes``.
    * ``data`` — freeform bag the executor may return for diagnostics /
      audit. Runner treats it as opaque; not persisted in A3a.
    """

    ok: bool
    findings_new: int = 0
    findings_unvalidated: int = 0
    findings_high_severity: int = 0
    findings_total: int = 0
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class PlaybookExecutor(Protocol):
    """Interface every executor honors.

    ``scope_context`` = the analyst-declared scope item the step is running
    against (a single scope_item_id — the runner iterates over the run's
    ``scope_subset`` and calls ``run_step`` per item per step). Executors
    substitute ``{{scope_item}}`` in the args template with this value.
    """

    def run_step(
        self,
        *,
        tool_slug: str,
        args_template: Mapping[str, Any],
        scope_context: str,
    ) -> StepResult:
        ...


class InternalExecutor:
    """In-process executor stub — A3a seam only.

    ``run_step`` raises ``NotImplementedError`` for real tool slugs; A3b binds
    it to ``services.tool_invocation``. The class exists so the runner's
    dependency-injection shape is final in A3a and A3b is a pure wire-up.

    Tests drive the runner with a ``MockExecutor`` returning canned
    ``StepResult`` values — that's the intended usage until A3b.
    """

    def run_step(
        self,
        *,
        tool_slug: str,
        args_template: Mapping[str, Any],
        scope_context: str,
    ) -> StepResult:
        raise NotImplementedError(
            "InternalExecutor.run_step is stubbed in A3a; wire to the tool "
            "registry in A3b before invoking against real playbooks."
        )


def substitute_scope(
    args_template: Mapping[str, Any], scope_context: str
) -> dict[str, Any]:
    """Replace ``{{scope_item}}`` in string values of the args template.

    Kept as a plain function so the mock executor can reuse the same
    substitution the internal executor will. Non-string values pass through
    untouched. Nested dicts/lists are traversed shallowly — playbook step
    args templates are flat by convention in A3a.
    """
    out: dict[str, Any] = {}
    for k, v in args_template.items():
        if isinstance(v, str):
            out[k] = v.replace("{{scope_item}}", scope_context)
        else:
            out[k] = v
    return out
