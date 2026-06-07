"""
Per-coin checkpoint file for resumable backtest replays.

One pickle per coin at runs/<run_id>/checkpoint/<COIN>.pkl carrying:
  - last_completed_epoch_ts: epoch boundary already replayed in full
  - exchange:                BacktestExchange.to_state() dict
  - trader:                  BacktestTrader.to_state() dict
  - thinker:                 ThinkerState as a plain dict
  - fills:                   accumulated fill log (list of dicts)
  - series:                  accumulated snapshot log (list of dicts)

Writes are atomic (write-to-tmp + os.replace). The checkpoint is the
authoritative state — fills/series parquets in runs/<run_id>/fills,series
are derived from it for inspection and are rewritten on every epoch flush.

On resume, the engine loads the checkpoint, restores in-memory state, and
fast-forwards the epoch loop past `last_completed_epoch_ts`. A coin with
no checkpoint starts fresh as before.
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Optional

from .. import workspace as ws


CHECKPOINT_VERSION = 1


def checkpoint_path(run_id: str, coin: str) -> Path:
    return ws.ensure_dir(ws.run_dir(run_id) / "checkpoint") / f"{coin.upper()}.pkl"


def save(
    run_id: str,
    coin: str,
    last_completed_epoch_ts: float,
    exchange_state: dict,
    trader_state: dict,
    thinker_state: dict,
    fills: list,
    series: list,
) -> None:
    """Atomic write of full backtest state for one coin."""
    payload = {
        "version": CHECKPOINT_VERSION,
        "coin": coin.upper(),
        "last_completed_epoch_ts": float(last_completed_epoch_ts),
        "exchange": exchange_state,
        "trader": trader_state,
        "thinker": thinker_state,
        "fills": fills,
        "series": series,
    }
    path = checkpoint_path(run_id, coin)
    tmp = path.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def load(run_id: str, coin: str) -> Optional[dict]:
    """Read the latest checkpoint for (run_id, coin). None if missing/corrupt."""
    path = checkpoint_path(run_id, coin)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
        return None
    return data
