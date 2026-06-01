"""Training driver for the backtest.

Walks 14-day epochs from each coin's earliest viable date (when it first
has MIN_CANDLES 1-week candles) up to a `until_ts` end-cap. For each
(coin, epoch) it invokes the production trainer with `asof_ts` set so
no post-epoch data leaks into the training.

Output: backtest/runs/<run_id>/training/<YYYYMMDD>/<COIN>/training_data.json
(plus the trainer's other artifacts, written via prod paths).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

import pandas as pd

from pt_pricesource import ArcticPriceSource
from pt_trainer import MIN_CANDLES, TrainerConfig, TrainingLoop

from . import workspace as ws


EPOCH_DAYS = 14
ONE_WEEK_MINUTES = 10080
DEFAULT_SOURCE = "kucoin"  # shared ArcticDB store


@dataclass
class EpochResult:
    coin: str
    asof_ts: float
    asof: pd.Timestamp
    ok: bool
    error: Optional[str] = None
    skipped: bool = False  # training_data.json already existed at start


def earliest_viable_asof(
    coin: str, price_source: ArcticPriceSource,
) -> Optional[pd.Timestamp]:
    """First timestamp at which `coin` has MIN_CANDLES weekly bars available.

    Returns None when the coin can't be trained yet:
      - 1w library is missing the symbol (e.g. not backfilled),
      - fewer than MIN_CANDLES weekly bars exist.

    Callers should treat None as "skip this coin" — the train_grid loop
    yields no epochs and the coin appears with 0 in the summary.
    """
    try:
        df = price_source.get_candles(coin, ONE_WEEK_MINUTES)
    except Exception:
        # Missing library or missing symbol — both surface as ArcticDB
        # exceptions. Either way the coin isn't viable here.
        return None
    if len(df) < MIN_CANDLES:
        return None
    # asof is an *exclusive* cutoff, so to include the 100th bar we need a
    # timestamp strictly after it. Use the bar boundary that opens the next
    # week (index[MIN_CANDLES]) so MIN_CANDLES bars are visible to the trainer.
    if len(df) > MIN_CANDLES:
        return df.index[MIN_CANDLES].to_pydatetime().replace(tzinfo=df.index.tz)
    # Exactly MIN_CANDLES bars: use the last bar's timestamp + 1 week
    last = df.index[-1]
    return (last + pd.Timedelta(minutes=ONE_WEEK_MINUTES)).to_pydatetime().replace(tzinfo=df.index.tz)


def epoch_schedule(
    coin: str,
    until: pd.Timestamp,
    price_source: ArcticPriceSource,
    epoch_days: int = EPOCH_DAYS,
) -> Iterator[pd.Timestamp]:
    """Yield epoch asof timestamps for `coin` from earliest-viable to `until`,
    stepping `epoch_days` apart. Each yielded timestamp is the *exclusive*
    cutoff for the trainer (no candle with index >= asof is seen)."""
    start = earliest_viable_asof(coin, price_source)
    if start is None:
        return
    start = pd.Timestamp(start)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    until = pd.Timestamp(until)
    if until.tzinfo is None:
        until = until.tz_localize("UTC")
    step = pd.Timedelta(days=epoch_days)
    t = start
    while t <= until:
        yield t
        t = t + step


def train_one_epoch(
    run_id: str,
    coin: str,
    asof_ts: float,
    data_source: str = DEFAULT_SOURCE,
    skip_if_done: bool = True,
) -> EpochResult:
    """Run pt_trainer for a single (coin, asof) into the backtest workspace.

    If `skip_if_done` and training_data.json already exists for this
    (run, coin, asof), returns ok=True without re-running — enables
    resume by passing the same run_id across invocations.

    Catches both Exception and SystemExit (pt_trainer uses sys.exit on
    failure, which would otherwise terminate the whole CLI). KeyboardInterrupt
    still propagates so Ctrl-C works.
    """
    asof = pd.Timestamp(asof_ts, unit="s", tz="UTC")
    epoch_dir = ws.training_epoch_dir(run_id, asof_ts, coin)

    if skip_if_done and (epoch_dir / "training_data.json").exists():
        return EpochResult(
            coin=coin.upper(), asof_ts=asof_ts, asof=asof, ok=True, skipped=True,
        )

    config = TrainerConfig(
        coin=coin.upper(),
        data_source=data_source,
        reprocess=True,
        verbose=False,
        asof_ts=asof_ts,
    )
    try:
        with ws.chdir(epoch_dir):
            TrainingLoop(config).run()
        return EpochResult(coin=coin.upper(), asof_ts=asof_ts, asof=asof, ok=True)
    except KeyboardInterrupt:
        raise
    except SystemExit as e:
        return EpochResult(
            coin=coin.upper(), asof_ts=asof_ts, asof=asof,
            ok=False, error=f"SystemExit (pt_trainer sys.exit code={e.code})",
        )
    except Exception as e:
        return EpochResult(
            coin=coin.upper(), asof_ts=asof_ts, asof=asof,
            ok=False, error=f"{type(e).__name__}: {e}",
        )


def train_coin(
    run_id: str,
    coin: str,
    until: pd.Timestamp,
    price_source: Optional[ArcticPriceSource] = None,
    data_source: str = DEFAULT_SOURCE,
) -> list[EpochResult]:
    """Walk all 14-day epochs for `coin` up to `until`, returning per-epoch
    results. Serial — Ray parallelism is layered on by callers."""
    if price_source is None:
        price_source = ArcticPriceSource()
    results: list[EpochResult] = []
    for asof in epoch_schedule(coin, until, price_source):
        results.append(train_one_epoch(run_id, coin, asof.timestamp(), data_source))
    return results


# ──────────────────────────────────────────────────────────────────────────
# Parallel training grid (coin × epoch). Independent units → Ray-parallel.
#
# Why this works safely under Ray:
#  - Ray workers are separate OS processes; each has its own CWD, so the
#    chdir in train_one_epoch can't collide.
#  - The ArcticDB store is opened lmdb-mode and only READ here; lmdb
#    supports unlimited concurrent readers across processes.
#  - skip_if_done lets re-launches resume without redoing work.
# ──────────────────────────────────────────────────────────────────────────


def _train_one_remote(
    run_id: str, coin: str, asof_ts: float,
    data_source: str = DEFAULT_SOURCE,
) -> EpochResult:
    """Top-level picklable target for Ray. Avoids capturing closures."""
    return train_one_epoch(run_id, coin, asof_ts, data_source)


def train_grid(
    run_id: str,
    coins: list[str],
    until: pd.Timestamp,
    parallel: bool = True,
    epochs_per_coin: Optional[int] = None,
    price_source: Optional[ArcticPriceSource] = None,
    data_source: str = DEFAULT_SOURCE,
) -> dict[str, list[EpochResult]]:
    """Train every (coin × epoch) up to `until`.

    Each (coin, asof) is an independent task and parallelizable across
    Ray workers. Reuses existing training_data.json via skip_if_done.

    Returns a dict mapping coin → list of EpochResult (asof-ordered).
    """
    if price_source is None:
        price_source = ArcticPriceSource()

    # Build the full task list across coins
    tasks: list[tuple[str, float]] = []
    schedules: dict[str, list[pd.Timestamp]] = {}
    untrainable: list[str] = []
    for coin in coins:
        sched = list(epoch_schedule(coin, until, price_source))
        if not sched:
            untrainable.append(coin)
        if epochs_per_coin is not None:
            sched = sched[:epochs_per_coin]
        schedules[coin] = sched
        for asof in sched:
            tasks.append((coin, asof.timestamp()))

    if untrainable:
        print(f"[train_grid] skipping {len(untrainable)} coin(s) with no "
              f"viable epochs (missing 1w data or <100 weekly bars): "
              f"{', '.join(untrainable)}")

    if not tasks:
        return {c: [] for c in coins}

    use_ray = parallel
    if use_ray:
        try:
            import ray  # type: ignore
        except ImportError:
            print("[train_grid] Ray not installed — falling back to serial")
            use_ray = False

    if use_ray:
        import ray  # type: ignore
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, log_to_driver=False)
        remote = ray.remote(_train_one_remote)
        futures = [remote.remote(run_id, c, ts, data_source) for c, ts in tasks]
        results: list[EpochResult] = ray.get(futures)
    else:
        results = [_train_one_remote(run_id, c, ts, data_source) for c, ts in tasks]

    by_coin: dict[str, list[EpochResult]] = {c: [] for c in coins}
    for (c, _), res in zip(tasks, results):
        by_coin[c].append(res)
    return by_coin
