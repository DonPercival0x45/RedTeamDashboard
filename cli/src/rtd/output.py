"""Output helpers — pretty tables by default, raw JSON when ``--json`` is set."""
from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any

from rich.console import Console
from rich.table import Table

# stderr console so piped JSON output isn't polluted with rich's status spinners.
console = Console()
err_console = Console(stderr=True)


def emit(payload: Any, *, json_mode: bool, table: Table | None = None) -> None:
    """Print ``table`` to stdout when human-mode; ``payload`` as JSON otherwise."""
    if json_mode or table is None:
        json.dump(payload, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return
    console.print(table)


def kv_table(title: str, rows: Iterable[tuple[str, Any]]) -> Table:
    """Two-column key/value table — handy for `rtd engagement view` output."""
    t = Table(title=title, show_header=False, box=None)
    t.add_column("key", style="bold")
    t.add_column("value")
    for k, v in rows:
        t.add_row(k, "" if v is None else str(v))
    return t


def error(msg: str) -> None:
    err_console.print(f"[bold red]error:[/bold red] {msg}")
