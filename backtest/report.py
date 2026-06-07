"""
Per-run reporting for the backtest CLI.

Produces two artifacts under runs/<run_id>/:
  - report.jsonl   one JSON event per line, append-only, written live as
                   the run proceeds. Tail-friendly for monitoring.
  - report.txt     human-readable summary written at end-of-run.

Events written to report.jsonl
------------------------------
{"ts": "...", "event": "run_started",      "subcommand": "run", "coins": [...],
                                            "params": {...}, "run_id": "..."}
{"ts": "...", "event": "task_started",     "coin": "BTC", "pid": 12345}
{"ts": "...", "event": "task_completed",   "coin": "BTC", "elapsed_s": 1234.5,
                                            "n_trained": 5, "n_skipped": 170,
                                            "n_failed": 0, "epochs_used": 175,
                                            "fills": 47, "snapshots": 58800,
                                            "pct_return": 12.3}
{"ts": "...", "event": "task_error",       "coin": "XYZ", "error": "...",
                                            "elapsed_s": 12.3}
{"ts": "...", "event": "run_completed",    "elapsed_s": ..., "n_ok": ...,
                                            "n_error": ...}
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Optional

from . import workspace as ws


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report_path_jsonl(run_id: str) -> Path:
    return ws.ensure_dir(ws.run_dir(run_id)) / "report.jsonl"


def report_path_txt(run_id: str) -> Path:
    return ws.ensure_dir(ws.run_dir(run_id)) / "report.txt"


def event(run_id: str, name: str, **fields) -> None:
    """Append one event line to runs/<run_id>/report.jsonl. Atomic per line."""
    payload = {"ts": _now_iso(), "event": name, **fields}
    line = json.dumps(payload, default=str) + "\n"
    # Append-mode opens are atomic for writes < PIPE_BUF (4096 bytes on
    # Linux). One event payload is well below that, so concurrent appends
    # from the orchestrator + Ray driver don't interleave.
    with open(report_path_jsonl(run_id), "a") as f:
        f.write(line)


def write_summary_text(run_id: str) -> None:
    """Read report.jsonl and emit a human-readable runs/<run_id>/report.txt."""
    jp = report_path_jsonl(run_id)
    if not jp.exists():
        return
    events = []
    for line in jp.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    by_kind: dict = {}
    for e in events:
        by_kind.setdefault(e.get("event"), []).append(e)

    started = (by_kind.get("run_started") or [{}])[0]
    completed = (by_kind.get("run_completed") or [{}])[-1]
    task_done = by_kind.get("task_completed") or []
    task_err = by_kind.get("task_error") or []

    lines: list[str] = []
    lines.append(f"PowerTrader backtest report — {run_id}")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"Subcommand:     {started.get('subcommand', '?')}")
    lines.append(f"Started:        {started.get('ts', '?')}")
    lines.append(f"Completed:      {completed.get('ts', '?')}")
    lines.append(f"Total elapsed:  {completed.get('elapsed_s', 0):.1f}s")
    coins = started.get("coins") or []
    lines.append(f"Coins ({len(coins)}):     {', '.join(coins)}")
    params = started.get("params") or {}
    if params:
        lines.append(f"Params:         lvl={params.get('lvl')} alloc={params.get('alloc')}% pm={params.get('pm')}%")
    lines.append("")
    lines.append(f"Results: {len(task_done)} ok, {len(task_err)} error")
    lines.append("")

    if task_done:
        lines.append("Successful coins:")
        lines.append("  COIN   trained skipped failed epochs   fills    snapshots   return%   elapsed_s")
        for e in sorted(task_done, key=lambda r: r.get("coin", "")):
            lines.append(
                f"  {e.get('coin','?'):<5}  "
                f"{e.get('n_trained',0):>5}   "
                f"{e.get('n_skipped',0):>5}   "
                f"{e.get('n_failed',0):>4}  "
                f"{e.get('epochs_used',0):>5}  "
                f"{e.get('fills',0):>6}    "
                f"{e.get('snapshots',0):>8}   "
                f"{e.get('pct_return',0.0):+7.2f}   "
                f"{e.get('elapsed_s',0.0):>9.1f}"
            )
        lines.append("")

    if task_err:
        lines.append("Errors:")
        for e in sorted(task_err, key=lambda r: r.get("coin", "")):
            lines.append(f"  {e.get('coin','?'):<5}  {e.get('error','?')}")
        lines.append("")

    lines.append(f"Full event log: {jp}")
    report_path_txt(run_id).write_text("\n".join(lines) + "\n")
