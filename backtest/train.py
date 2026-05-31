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


def earliest_viable_asof(
    coin: str, price_source: ArcticPriceSource,
) -> Optional[pd.Timestamp]:
    """First timestamp at which `coin` has MIN_CANDLES weekly bars available.

    Returns None if the coin doesn't have enough 1w history yet.
    """
    df = price_source.get_candles(coin, ONE_WEEK_MINUTES)
    if len(df) < MIN_CANDLES:
        return None
    return df.index[MIN_CANDLES - 1].to_pydatetime().replace(tzinfo=df.index.tz)


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
) -> EpochResult:
    """Run pt_trainer for a single (coin, asof) into the backtest workspace."""
    asof = pd.Timestamp(asof_ts, unit="s", tz="UTC")
    epoch_dir = ws.training_epoch_dir(run_id, asof_ts, coin)

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
