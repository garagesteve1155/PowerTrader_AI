"""
3D parameter sweep over (trade_start_level, start_allocation_pct, pm_start_pct).

Phases
------
A) Train all (coin, asof) once per coin via pt_trainer. Param-independent,
   so we train every coin at every 14-day epoch up to `until` and reuse
   the per-epoch artifacts across all 350 param points.

B) For each (coin, params) run the engine. Each task is independent →
   Ray-parallel if available, serial otherwise.

Sweep grid (defaults; override in run())
  trade_start_level:    1..7
  start_allocation_pct: 1..5
  pm_start_pct:         1..10
                        = 7 × 5 × 10 = 350 points
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable, List, Optional

import pandas as pd

from pt_pricesource import ArcticPriceSource

from . import workspace as ws
from .engine import BacktestParams, CoinRunConfig, run_coin
from .train import epoch_schedule, train_grid, train_one_epoch


def default_grid() -> List[BacktestParams]:
    grid = []
    for lvl in range(1, 8):
        for alloc in range(1, 6):
            for pm in range(1, 11):
                grid.append(BacktestParams(
                    trade_start_level=lvl,
                    start_allocation_pct=float(alloc),
                    pm_start_pct=float(pm),
                ))
    return grid


def _train_coin_phase(
    run_id: str, coin: str, until: pd.Timestamp,
    price_source: ArcticPriceSource,
    parallel: bool = True,
) -> List[pd.Timestamp]:
    """Train every epoch for `coin` up to `until` via train_grid (Ray-parallel
    across epochs by default). Returns the schedule the engine should walk."""
    schedule = list(epoch_schedule(coin, until, price_source))
    train_grid(
        run_id=run_id, coins=[coin], until=until,
        parallel=parallel, price_source=price_source,
    )
    return schedule


@dataclass
class SweepResult:
    run_id: str
    coin: str
    params: BacktestParams
    epochs_used: int
    rows_fills: int
    rows_series: int
    error: Optional[str] = None


def _run_one(
    run_id: str, coin: str, params: BacktestParams,
    until: pd.Timestamp, schedule: list,
) -> SweepResult:
    cfg = CoinRunConfig(
        coin=coin,
        starting_usd=1000.0,
        until=until,
        record_every_n=12,
        params=params,
    )
    # Each task gets its own price source (Arctic readers are process-safe).
    res = run_coin(run_id, cfg, epoch_schedule=schedule, price_source=ArcticPriceSource())
    return SweepResult(
        run_id=run_id, coin=coin, params=params,
        epochs_used=res.epochs_used,
        rows_fills=len(res.fills),
        rows_series=len(res.series),
        error=res.error,
    )


def run_coin_sweep(
    run_id: str,
    coin: str,
    until: Optional[pd.Timestamp] = None,
    grid: Optional[List[BacktestParams]] = None,
    parallel: bool = True,
) -> List[SweepResult]:
    """Train + sweep one coin across all param points.

    Each param-point's output lands in a separate sub-run-id so they don't
    collide:  runs/<run_id>__<coin>__l<L>_a<A>_p<P>/
    """
    if until is None:
        until = pd.Timestamp.utcnow().tz_localize("UTC") if pd.Timestamp.utcnow().tz is None else pd.Timestamp.utcnow()
    if grid is None:
        grid = default_grid()

    src = ArcticPriceSource()
    schedule = _train_coin_phase(run_id, coin, until, src)

    tasks = [
        (run_id + f"__{coin}__l{p.trade_start_level}_a{int(p.start_allocation_pct)}_p{int(p.pm_start_pct)}",
         coin, p, until, schedule)
        for p in grid
    ]

    if parallel:
        try:
            import ray
            if not ray.is_initialized():
                ray.init(ignore_reinit_error=True, log_to_driver=False)
            remote_one = ray.remote(_run_one)
            futures = [remote_one.remote(*t) for t in tasks]
            return ray.get(futures)
        except ImportError:
            pass
        except Exception as e:
            print(f"Ray init failed ({e}); falling back to serial")

    return [_run_one(*t) for t in tasks]
