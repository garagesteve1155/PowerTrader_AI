"""Backtest workspace path helpers.

Layout
------
backtest/runs/<run_id>/
  training/<YYYYMMDD>/<COIN>/training_data.json   # one set per 14-day epoch
  fills/<COIN>.parquet                            # trade log per coin
  series/<COIN>.parquet                           # hourly pnl/invested/total
  meta.json                                       # run config snapshot

The training subtree mirrors what the production trainer writes when
launched from a per-coin CWD. The backtest invokes the trainer with
CWD set to `training/<asof>/<COIN>/`, so existing prod path I/O works
without modification.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Iterator, Optional


def project_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def runs_root() -> Path:
    return project_dir() / "backtest" / "runs"


def run_dir(run_id: str) -> Path:
    return runs_root() / run_id


def training_epoch_dir(run_id: str, asof_ts: float, coin: str) -> Path:
    """`training/<YYYYMMDD>/<COIN>/` for a given epoch + coin."""
    stamp = datetime.fromtimestamp(asof_ts, tz=timezone.utc).strftime("%Y%m%d")
    return run_dir(run_id) / "training" / stamp / coin.upper()


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def chdir(target: Path) -> Iterator[Path]:
    """Temporarily chdir into `target`. Restores previous CWD on exit."""
    prev = Path.cwd()
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    os.chdir(target)
    try:
        yield target
    finally:
        os.chdir(prev)


def new_run_id(prefix: Optional[str] = None) -> str:
    """Timestamped run id, e.g. `20260601_120000` or `pilot_20260601_120000`."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}" if prefix else stamp
