"""
Backtest replay engine.

Per coin, walks the 5min `kucoin5` price grid across consecutive 14-day
training epochs. At each 5min boundary T:
  1) For each trained TF: find the in-progress bar (open = bar_start
     open price, close = 5min bar open at T as live-price proxy).
  2) score_tf → compute_tf_prices → rebuild_bounds → vote_one across TFs.
  3) Push (long_count, short_count, low_bound_prices) onto the trader.
  4) Update BacktestExchange with the current bar's open (live price) +
     fill price (= 5min bar close, per the "decide at open, fill at close"
     spec).
  5) Tick BacktestTrader.manage_trades().
  6) Snapshot state (cash, holdings, equity).

Training_data swaps every 14 days when a new epoch starts (uses pre-
trained artifacts from backtest/runs/<run_id>/training/<YYYYMMDD>/<COIN>/).

Outputs (per coin, written to runs/<run_id>/):
  fills/<COIN>.parquet      — chronological fill log
  series/<COIN>.parquet     — per-5min state snapshots
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

import pt_trader
from pt_env import TRAIN_TF_MINUTES, TRAIN_TF_NAMES
from pt_pricesource import ArcticPriceSource

from . import checkpoint as ckpt
from . import thinker as bt_thinker
from . import workspace as ws
from .exchange import BacktestExchange
from .trader import BacktestTrader


FIVE_MIN = 300
TF_MINUTES = list(TRAIN_TF_MINUTES)
TF_NAMES = list(TRAIN_TF_NAMES)


@dataclass
class BacktestParams:
    """The 3D sweep dimensions. Defaults match prod pt_config.json."""
    trade_start_level: int = 2
    start_allocation_pct: float = 1.0
    pm_start_pct: float = 4.0   # used for both no_dca and with_dca per user spec


@dataclass
class CoinRunConfig:
    coin: str
    starting_usd: float = 1000.0
    until: Optional[pd.Timestamp] = None
    epochs_limit: Optional[int] = None
    record_every_n: int = 12   # snapshot every N×5min (12 = hourly)
    params: BacktestParams = field(default_factory=BacktestParams)


@dataclass
class CoinRunResult:
    coin: str
    fills: pd.DataFrame
    series: pd.DataFrame             # per-snapshot rows
    epochs_used: int                 # NEW epochs replayed in this call
    error: Optional[str] = None
    # Lifetime counts — combine this call + prior checkpointed work
    epochs_resumed: int = 0          # epochs already done before this call (from checkpoint)
    epochs_total: int = 0            # total epochs in the coin's full schedule
    # Date ranges (pre-strftime ISO dates as strings, so JSON-safe)
    schedule_first_epoch: Optional[str] = None
    schedule_last_epoch: Optional[str] = None
    replayed_through: Optional[str] = None    # last epoch whose bars were walked
    resumed_from: Optional[str] = None        # epoch the checkpoint last completed


# ----------------------------------------------------------------------
# Bar alignment helpers
# ----------------------------------------------------------------------

def bar_start_for_tf(t_unix: float, tf_minutes: int) -> int:
    """Unix epoch sec of the bar containing time t for a given TF."""
    secs = tf_minutes * 60
    return int(t_unix // secs) * secs


# ----------------------------------------------------------------------
# Per-coin run
# ----------------------------------------------------------------------

def run_coin(
    run_id: str,
    cfg: CoinRunConfig,
    epoch_schedule: List[pd.Timestamp],
    price_source: Optional[ArcticPriceSource] = None,
) -> CoinRunResult:
    """Replay backtest for one coin given a pre-computed epoch schedule.

    Each entry in `epoch_schedule` is an asof timestamp matching what was
    used at training time. The engine walks 5min bars from epoch[i] to
    min(epoch[i+1], cfg.until), using training_data.json written at
    epoch[i]. The first epoch is the start of the live tradable period.
    """
    coin = cfg.coin.upper()
    if price_source is None:
        price_source = ArcticPriceSource()

    # Inject sweep params into the prod globals (used by Trader.__init__ defaults
    # — we mirror them on the instance, but the *_refresh_paths_and_symbols path
    # in manage_trades also re-reads from pt_config.json globals each tick, so
    # set them as authoritative).
    pt_trader.TRADE_START_LEVEL = int(cfg.params.trade_start_level)
    pt_trader.START_ALLOC_PCT = float(cfg.params.start_allocation_pct)
    pt_trader.PM_START_PCT_NO_DCA = float(cfg.params.pm_start_pct)
    pt_trader.PM_START_PCT_WITH_DCA = float(cfg.params.pm_start_pct)
    # Limit coin universe to the single coin under test so unrelated bookkeeping is skipped.
    pt_trader.crypto_symbols = [coin]
    pt_trader.LONG_TERM_SYMBOLS = set()
    pt_trader.EXCLUDED_COINS = set()

    # Exchange + trader (lifetime: full backtest, not per epoch)
    ex = BacktestExchange(starting_usd=cfg.starting_usd)
    trader = BacktestTrader(ex)

    # Per-coin global state across epochs
    state = bt_thinker.ThinkerState.fresh(len(TF_NAMES))
    fills: List[dict] = []
    series: List[dict] = []
    resume_skip_until_ts: float = 0.0

    # Try to resume from a previous checkpoint
    _ckpt = ckpt.load(run_id, coin)
    if _ckpt is not None:
        try:
            ex.load_state(_ckpt["exchange"])
            trader.load_state(_ckpt["trader"])
            state = bt_thinker.ThinkerState(**_ckpt["thinker"])
            fills = list(_ckpt["fills"])
            series = list(_ckpt["series"])
            resume_skip_until_ts = float(_ckpt["last_completed_epoch_ts"])
            _last_epoch_date = pd.Timestamp(
                resume_skip_until_ts, unit="s", tz="UTC"
            ).date()
            print(f"[{coin}] resuming from checkpoint, last completed epoch: "
                  f"{_last_epoch_date}  ({len(fills)} fills, {len(series)} snapshots)")
        except Exception as e:
            print(f"[{coin}] checkpoint load failed ({e}); starting fresh")
            ex = BacktestExchange(starting_usd=cfg.starting_usd)
            trader = BacktestTrader(ex)
            state = bt_thinker.ThinkerState.fresh(len(TF_NAMES))
            fills, series = [], []
            resume_skip_until_ts = 0.0

    # 5min price grid for the whole window (one query per coin)
    try:
        grid = price_source.get_candles(coin, 5)
    except Exception as e:
        return CoinRunResult(
            coin=coin, fills=pd.DataFrame(), series=pd.DataFrame(),
            epochs_used=0,
            error=f"no kucoin5 data for {coin}: {e}",
        )
    if grid.empty:
        return CoinRunResult(
            coin=coin, fills=pd.DataFrame(), series=pd.DataFrame(),
            epochs_used=0,
            error=f"no kucoin5 data for {coin}",
        )

    # Per-TF candle frames (one query per TF; sliced cheaply per bar)
    tf_frames: Dict[int, pd.DataFrame] = {}
    for tf_min in TF_MINUTES:
        try:
            tf_frames[tf_min] = price_source.get_candles(coin, tf_min)
        except Exception as e:
            return CoinRunResult(
                coin=coin, fills=pd.DataFrame(), series=pd.DataFrame(),
                epochs_used=0,
                error=f"missing kucoin{tf_min} for {coin}: {e}",
            )

    until_ts = (
        cfg.until.timestamp() if cfg.until is not None
        else float(grid.index[-1].timestamp())
    )
    if cfg.epochs_limit is not None:
        epoch_schedule = epoch_schedule[: cfg.epochs_limit + 1]

    # Engine main loop: per epoch, walk 5min bars
    epochs_used = 0
    step_count = 0
    fills_dir = ws.ensure_dir(ws.run_dir(run_id) / "fills")
    series_dir = ws.ensure_dir(ws.run_dir(run_id) / "series")
    import os as _os
    _engine_pid = _os.getpid()
    _engine_tag = f"[engine pid={_engine_pid} coin={coin}]"
    print(f"{_engine_tag} bar-walk begin: {len(epoch_schedule)} epochs", flush=True)
    import time as _time
    _epoch_t0 = _time.monotonic()
    # Intra-epoch heartbeat: log every N bars within an epoch so a stuck
    # bar-walk can be pinpointed without waiting for the per-epoch
    # boundary log (which only fires once an epoch fully completes).
    _BAR_HEARTBEAT_EVERY = 500
    # Wall-clock watchdog: emit a WARN line if no bar-loop progress is
    # observed for this many seconds. Runs on a daemon THREAD (not in
    # the bar loop) so it fires even if the worker is hung inside a
    # single manage_trades call — which is exactly the failure mode we
    # care about.
    _STUCK_THRESHOLD_S = 120.0

    # Shared mutable progress marker. Index 0 is the wall-clock of the
    # last bar update; 1 is current epoch index; 2 is bar within epoch.
    _progress = [_time.monotonic(), 0, 0]

    import threading as _threading
    _watchdog_stop = _threading.Event()
    def _watchdog_thread():
        already_fired_for = -1  # epoch index of last warning emitted
        while not _watchdog_stop.wait(15.0):
            last_progress, ei_now, bar_now = (
                _progress[0], _progress[1], _progress[2]
            )
            gap = _time.monotonic() - last_progress
            if gap > _STUCK_THRESHOLD_S and ei_now != already_fired_for:
                already_fired_for = ei_now
                print(
                    f"{_engine_tag} WATCHDOG: no bar progress for {gap:.0f}s — "
                    f"stuck at epoch {ei_now}/{len(epoch_schedule)} "
                    f"bar {bar_now}. py-spy or gdb this pid to inspect.",
                    flush=True,
                )
    _threading.Thread(target=_watchdog_thread, daemon=True).start()
    for ei, epoch_start in enumerate(epoch_schedule):
        # Load training_data for THIS epoch
        epoch_ts = epoch_start.timestamp()
        # Resume: skip any epoch already completed in a previous run
        if epoch_ts <= resume_skip_until_ts:
            continue
        td_path = (
            ws.training_epoch_dir(run_id, epoch_ts, coin) / "training_data.json"
        )
        if not td_path.exists():
            # Skip epoch silently; trader keeps last training_data
            continue
        try:
            td_all = json.loads(td_path.read_text())
        except Exception:
            continue

        parsed = {
            tf: bt_thinker.parse_tf_training_data(td_all.get(tf, {}))
            for tf in TF_NAMES
        }

        # Walk 5min bars from epoch_start to next_epoch_start (or until)
        next_epoch_ts = (
            epoch_schedule[ei + 1].timestamp()
            if ei + 1 < len(epoch_schedule) else until_ts
        )
        epoch_end_ts = min(next_epoch_ts, until_ts)

        bars = grid[(grid.index >= epoch_start) & (grid.index.asi8 // 10**9 < epoch_end_ts)]
        if bars.empty:
            continue

        epochs_used += 1
        _bars_in_epoch = len(bars)
        _bar_idx_in_epoch = 0
        for bar_ts_pd, row in bars.iterrows():
            _bar_idx_in_epoch += 1
            # Refresh progress marker so the watchdog thread can detect
            # a stuck bar-walk independent of the heartbeat cadence.
            _progress[0] = _time.monotonic()
            _progress[1] = ei + 1
            _progress[2] = _bar_idx_in_epoch
            if _bar_idx_in_epoch % _BAR_HEARTBEAT_EVERY == 0:
                _hb_elapsed = _time.monotonic() - _epoch_t0
                print(f"{_engine_tag} epoch {ei + 1}/{len(epoch_schedule)} "
                      f"{epoch_start.date()} bar {_bar_idx_in_epoch}/{_bars_in_epoch} "
                      f"elapsed={_hb_elapsed:.1f}s", flush=True)
            T = float(bar_ts_pd.timestamp())
            live_price = float(row["open"])
            fill_price = float(row["close"])

            # ── Score each TF at time T ───────────────────────────────
            new_high_tf = list(state.high_tf_prices)
            new_low_tf = list(state.low_tf_prices)
            new_perfects = list(state.perfects)
            if len(new_high_tf) != len(TF_NAMES):
                new_high_tf = [0.0] * len(TF_NAMES)
                new_low_tf = [0.0] * len(TF_NAMES)
                new_perfects = ["inactive"] * len(TF_NAMES)

            for tf_idx, (tf_name, tf_min) in enumerate(zip(TF_NAMES, TF_MINUTES)):
                tf_df = tf_frames[tf_min]
                if tf_df.empty:
                    continue
                bar_start_unix = bar_start_for_tf(T, tf_min)
                bar_start_ts = pd.Timestamp(bar_start_unix, unit="s", tz="UTC")
                # Always use searchsorted + iloc — handles missing AND duplicated
                # timestamps. `searchsorted(side="right") - 1` returns the index
                # of the latest bar whose start is <= bar_start_ts. iloc returns
                # exactly one Series regardless of duplicates.
                pos = tf_df.index.searchsorted(bar_start_ts, side="right") - 1
                if pos < 0:
                    continue
                bar_row = tf_df.iloc[pos]
                open_p = float(bar_row["open"])
                # close = current live (5min open at T); thinker semantics
                hd, ld, status = bt_thinker.score_tf(parsed[tf_name], open_p, live_price)
                ht, lt = bt_thinker.compute_tf_prices(live_price, hd, ld, status)
                new_high_tf[tf_idx] = ht
                new_low_tf[tf_idx] = lt
                new_perfects[tf_idx] = status

            # Use PREVIOUS bounds for voting, then rebuild for next bar
            long_count = 0
            short_count = 0
            for i in range(len(TF_NAMES)):
                hb = state.high_bound_prices[i] if i < len(state.high_bound_prices) else 99999999999999999
                lb = state.low_bound_prices[i] if i < len(state.low_bound_prices) else 0.0
                vote = bt_thinker.vote_one(live_price, hb, lb, new_high_tf[i], new_low_tf[i])
                if vote == "long":
                    long_count += 1
                elif vote == "short":
                    short_count += 1

            # Rebuild bounds from the just-scored TF prices for the NEXT bar
            high_bounds, low_bounds = bt_thinker.rebuild_bounds(
                new_high_tf, new_low_tf, new_perfects,
            )
            # Defensive pad in case rebuild collapsed duplicates
            n_tfs = len(TF_NAMES)
            if len(high_bounds) < n_tfs:
                high_bounds = list(high_bounds) + [99999999999999999] * (n_tfs - len(high_bounds))
            if len(low_bounds) < n_tfs:
                low_bounds = list(low_bounds) + [0.0] * (n_tfs - len(low_bounds))

            # Long price levels for the trader (dedup + sort desc, like prod _read_long_price_levels)
            uniq = {round(v, 12): v for v in low_bounds if v is not None}
            long_levels = sorted(uniq.values(), reverse=True)

            # ── Drive the trader ──────────────────────────────────────
            trader.set_signals(coin, long_count, short_count, long_levels)
            ex.set_bar(coin, live_price, fill_price)
            ex.set_time(T)
            trader.set_now(T)
            trader.manage_trades()

            # ── Capture new fills (delta from prior log) ──────────────
            new_orders = ex.orders_log()[len(fills):]
            for o in new_orders:
                fills.append(o)

            # ── Snapshot state ────────────────────────────────────────
            step_count += 1
            if cfg.record_every_n and (step_count % cfg.record_every_n == 0):
                holdings = ex.get_holdings()
                qty = holdings.get(coin, 0.0)
                pos_val = qty * live_price
                cash = ex.get_buying_power() or 0.0
                series.append({
                    "ts": pd.Timestamp(T, unit="s", tz="UTC"),
                    "live_price": live_price,
                    "qty": qty,
                    "position_usd": pos_val,
                    "cash": cash,
                    "total_account_value": cash + pos_val,
                    "long_count": long_count,
                    "short_count": short_count,
                    "realized_pnl_cum": float(
                        trader._pnl_ledger.get("total_realized_profit_usd", 0.0) or 0.0
                    ),
                })

            # Persist updated state
            state.high_tf_prices = new_high_tf
            state.low_tf_prices = new_low_tf
            state.perfects = new_perfects
            state.high_bound_prices = high_bounds
            state.low_bound_prices = low_bounds

        # ── End of epoch — flush checkpoint + parquets ────────────────
        # Trader/exchange/thinker state are snapshotted before moving on,
        # so a Ctrl-C or crash here loses at most one 14-day window.
        try:
            ckpt.save(
                run_id=run_id, coin=coin,
                last_completed_epoch_ts=epoch_ts,
                exchange_state=ex.to_state(),
                trader_state=trader.to_state(),
                thinker_state={
                    "high_tf_prices": list(state.high_tf_prices),
                    "low_tf_prices": list(state.low_tf_prices),
                    "high_bound_prices": list(state.high_bound_prices),
                    "low_bound_prices": list(state.low_bound_prices),
                    "perfects": list(state.perfects),
                },
                fills=fills,
                series=series,
            )
            # Mirror to parquet for live inspection (cheap; sizes are small).
            if fills:
                pd.DataFrame(fills).to_parquet(fills_dir / f"{coin}.parquet")
            if series:
                pd.DataFrame(series).to_parquet(series_dir / f"{coin}.parquet")
        except Exception as _save_err:
            # Don't let a flush failure abort the run; log and continue.
            print(f"[{coin}] checkpoint flush at epoch {epoch_start.date()} "
                  f"failed: {_save_err}")

        # Per-epoch progress beacon — visible in Ray worker .out file so
        # a stuck replay can be pinpointed to a specific epoch.
        _dt = _time.monotonic() - _epoch_t0
        print(f"{_engine_tag} epoch {ei + 1}/{len(epoch_schedule)} "
              f"{epoch_start.date()} done in {_dt:.1f}s  "
              f"fills={len(fills)} snapshots={len(series)}", flush=True)
        _epoch_t0 = _time.monotonic()

    _watchdog_stop.set()

    fills_df = pd.DataFrame(fills)
    series_df = pd.DataFrame(series)
    if not fills_df.empty:
        fills_df.to_parquet(fills_dir / f"{coin}.parquet")
    if not series_df.empty:
        series_df.to_parquet(series_dir / f"{coin}.parquet")

    # Lifetime accounting: epochs_resumed = how many were past on disk
    # before this call started; epochs_total = whole schedule length.
    epochs_resumed = sum(
        1 for ep in epoch_schedule if ep.timestamp() <= resume_skip_until_ts
    )
    schedule_first = (
        epoch_schedule[0].strftime("%Y-%m-%d") if len(epoch_schedule) else None
    )
    schedule_last = (
        epoch_schedule[-1].strftime("%Y-%m-%d") if len(epoch_schedule) else None
    )
    resumed_from = (
        pd.Timestamp(resume_skip_until_ts, unit="s", tz="UTC").strftime("%Y-%m-%d")
        if resume_skip_until_ts else None
    )
    # last bar processed lives in series[-1] if any new snapshots; else
    # fall back to the checkpoint's last_completed_epoch_ts.
    if series:
        last_ts = series[-1]["ts"]
        if hasattr(last_ts, "strftime"):
            replayed_through = last_ts.strftime("%Y-%m-%d")
        else:
            replayed_through = str(last_ts)[:10]
    else:
        replayed_through = resumed_from

    return CoinRunResult(
        coin=coin, fills=fills_df, series=series_df,
        epochs_used=epochs_used,
        epochs_resumed=epochs_resumed,
        epochs_total=len(epoch_schedule),
        schedule_first_epoch=schedule_first,
        schedule_last_epoch=schedule_last,
        replayed_through=replayed_through,
        resumed_from=resumed_from,
    )
