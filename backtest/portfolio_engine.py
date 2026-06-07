"""
Joint multi-coin backtest replay engine.

ONE BacktestExchange, ONE BacktestTrader, ONE shared cash pool. All
coins are processed at every 5min bar in the master timeline. Buying
power exhausts naturally when many positions are open and a DCA fires
— there is no per-coin allocation bubble like the legacy per-coin
engine had.

Output schema (everything under runs/<run_id>/):
  fills.parquet     one row per fill, multi-coin
    columns: ts (UTC datetime), ts_iso, side, symbol, qty, price,
             notional, tag, order_id, cash_after
  series.parquet    one row per daily snapshot
    columns: ts, ts_iso, cash, total_position_usd, total_account_value,
             then per coin:  qty_<COIN>, position_usd_<COIN>

Daily snapshots are taken every snapshot_every_n bars (default 288 =
1 day). Per-coin daily attribution is derived from these two parquets
in backtest/portfolio_aggregate.py (Phase 4) so the engine's only job
is to record raw state.

All timestamps logged to stdout use PowerTrader's canonical
YYYY-MM-DDTHH:MM:SSZ ISO-8601 form, matching pt_env.utcnow().
"""

from __future__ import annotations

import bisect
import datetime as _dt
import json
import os
import pickle
import threading
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

import pt_trader
from pt_env import TRAIN_TF_MINUTES, TRAIN_TF_NAMES
from pt_pricesource import ArcticPriceSource

from . import thinker as bt_thinker
from . import workspace as ws
from .exchange import BacktestExchange
from .trader import BacktestTrader
from .train import epoch_schedule


TF_NAMES = list(TRAIN_TF_NAMES)
TF_MINUTES = list(TRAIN_TF_MINUTES)
_TS_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(ts) -> str:
    """Render a UTC datetime or Unix-seconds float in the canonical form."""
    if hasattr(ts, "strftime"):
        return ts.strftime(_TS_ISO_FMT)
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime(_TS_ISO_FMT)


@dataclass
class PortfolioParams:
    """Sweep dimensions (same shape as the legacy BacktestParams)."""
    trade_start_level: int = 2
    start_allocation_pct: float = 1.0
    pm_start_pct: float = 4.0


@dataclass
class PortfolioRunConfig:
    coins: List[str]
    starting_usd: float = 10_000.0
    until: Optional[pd.Timestamp] = None
    from_date: Optional[pd.Timestamp] = None
    snapshot_every_n: int = 288   # 288 × 5min = 24h = daily
    params: PortfolioParams = field(default_factory=PortfolioParams)


@dataclass
class PortfolioRunResult:
    fills: pd.DataFrame
    series: pd.DataFrame
    coins_active: List[str]
    coins_skipped: List[str]
    bars_processed: int
    bars_resumed: int = 0
    error: Optional[str] = None


_CHECKPOINT_VERSION = 1


def _checkpoint_path(run_id: str):
    return ws.ensure_dir(ws.run_dir(run_id)) / "portfolio_checkpoint.pkl"


def _save_checkpoint(
    run_id: str,
    last_bar_ts: float,
    exchange_state: dict,
    trader_state: dict,
    thinker_states: dict,
    cached_epoch_ts: dict,
    fills: list,
    series: list,
) -> None:
    """Atomic write of full joint-engine state."""
    payload = {
        "version": _CHECKPOINT_VERSION,
        "last_completed_bar_ts": float(last_bar_ts),
        "exchange": exchange_state,
        "trader": trader_state,
        "thinker_states": thinker_states,
        "cached_epoch_ts": cached_epoch_ts,
        "fills": fills,
        "series": series,
    }
    path = _checkpoint_path(run_id)
    tmp = path.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def _load_checkpoint(run_id: str) -> Optional[dict]:
    path = _checkpoint_path(run_id)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("version") != _CHECKPOINT_VERSION:
        return None
    return data


# ----------------------------------------------------------------------
# Bar-alignment helpers
# ----------------------------------------------------------------------

def _bar_start_for_tf(t_unix: float, tf_minutes: int) -> int:
    secs = tf_minutes * 60
    return int(t_unix // secs) * secs


# ----------------------------------------------------------------------
# The big function
# ----------------------------------------------------------------------

def run_portfolio(
    run_id: str,
    cfg: PortfolioRunConfig,
    price_source: Optional[ArcticPriceSource] = None,
) -> PortfolioRunResult:
    """Walk the union 5min grid, score every live coin per bar, tick a
    single shared trader."""

    if price_source is None:
        price_source = ArcticPriceSource()

    tag = f"[portfolio pid={os.getpid()}]"

    def _log(msg: str) -> None:
        print(f"{tag} {_iso(_dt.datetime.now(_dt.timezone.utc))} {msg}",
              flush=True)

    _log(f"start run_id={run_id}  coins={len(cfg.coins)}  "
         f"starting=${cfg.starting_usd:,.0f}  "
         f"params=lvl{cfg.params.trade_start_level}_"
         f"a{cfg.params.start_allocation_pct}_p{cfg.params.pm_start_pct}")

    until_ts = (
        cfg.until.timestamp() if cfg.until is not None
        else _dt.datetime.now(_dt.timezone.utc).timestamp()
    )

    # ── Per-coin metadata: schedule, viability, kucoin5 head ─────────
    coin_meta: Dict[str, dict] = {}
    skipped: List[str] = []
    for c in cfg.coins:
        c = c.upper()
        sched = list(epoch_schedule(c, pd.Timestamp(until_ts, unit="s", tz="UTC"),
                                    price_source))
        if not sched:
            skipped.append(c)
            continue
        try:
            grid5 = price_source.get_candles(c, 5)
        except Exception as e:
            _log(f"skip {c}: kucoin5 unavailable ({type(e).__name__}: {e})")
            skipped.append(c)
            continue
        if grid5.empty:
            skipped.append(c)
            continue
        if cfg.from_date is not None:
            grid5 = grid5[grid5.index >= cfg.from_date]
            if grid5.empty:
                skipped.append(c)
                continue
        coin_meta[c] = {
            "sched": sched,
            "epoch_starts": [s.timestamp() for s in sched],
            "grid5": grid5,
            "first_ts": grid5.index[0],
        }

    if skipped:
        _log(f"skipped {len(skipped)} coin(s) with no viable epochs or no "
             f"kucoin5 data: {', '.join(sorted(skipped))}")
    active = sorted(coin_meta.keys())
    if not active:
        _log("no active coins, nothing to do")
        return PortfolioRunResult(
            fills=pd.DataFrame(), series=pd.DataFrame(),
            coins_active=[], coins_skipped=skipped, bars_processed=0,
            error="no active coins",
        )
    _log(f"active coins ({len(active)}): {', '.join(active)}")

    # ── Per-coin per-TF candle frames (~196 dataframes for 28 coins) ─
    tf_frames: Dict[tuple, pd.DataFrame] = {}
    for c in active:
        for tf_min in TF_MINUTES:
            try:
                tf_frames[(c, tf_min)] = price_source.get_candles(c, tf_min)
            except Exception as e:
                _log(f"WARN: {c} kucoin{tf_min} missing ({e}); coin disabled")
                # Drop this coin from active.
                active = [x for x in active if x != c]
                skipped.append(c)
                break
    _log(f"loaded {len(tf_frames)} TF candle frames")

    # ── Master timeline = union of all coins' kucoin5 indices ────────
    _t0 = _time.monotonic()
    all_idx = pd.DatetimeIndex(
        sorted(set().union(*[coin_meta[c]["grid5"].index for c in active]))
    )
    if cfg.from_date is not None:
        all_idx = all_idx[all_idx >= cfg.from_date]
    if cfg.until is not None:
        all_idx = all_idx[all_idx <= cfg.until]
    _log(f"master grid: {len(all_idx):,} bars  "
         f"{_iso(all_idx[0])} → {_iso(all_idx[-1])}  "
         f"(built in {_time.monotonic() - _t0:.1f}s)")

    # ── Trader / exchange / per-coin thinker state ───────────────────
    pt_trader.TRADE_START_LEVEL = int(cfg.params.trade_start_level)
    pt_trader.START_ALLOC_PCT = float(cfg.params.start_allocation_pct)
    pt_trader.PM_START_PCT_NO_DCA = float(cfg.params.pm_start_pct)
    pt_trader.PM_START_PCT_WITH_DCA = float(cfg.params.pm_start_pct)
    pt_trader.crypto_symbols = list(active)
    pt_trader.EXCLUDED_COINS = set()
    # LTH inherits from prod pt_config.json (already loaded into
    # pt_trader.LONG_TERM_SYMBOLS at import time). Leave it.

    ex = BacktestExchange(starting_usd=cfg.starting_usd)
    trader = BacktestTrader(ex)
    thinker_state: Dict[str, bt_thinker.ThinkerState] = {
        c: bt_thinker.ThinkerState.fresh(len(TF_NAMES)) for c in active
    }
    cached_epoch_ts: Dict[str, Optional[float]] = {c: None for c in active}
    parsed_td: Dict[str, Optional[dict]] = {c: None for c in active}

    fills: List[dict] = []
    series: List[dict] = []

    # Resume from checkpoint if present
    resume_skip_until_ts: float = 0.0
    bars_resumed = 0
    _ckpt = _load_checkpoint(run_id)
    if _ckpt is not None:
        try:
            ex.load_state(_ckpt["exchange"])
            trader.exchange = ex
            trader.load_state(_ckpt["trader"])
            for c, st_dict in _ckpt["thinker_states"].items():
                if c in thinker_state:
                    thinker_state[c] = bt_thinker.ThinkerState(**st_dict)
            for c, ets in _ckpt["cached_epoch_ts"].items():
                if c in cached_epoch_ts:
                    cached_epoch_ts[c] = ets
            fills = list(_ckpt["fills"])
            series = list(_ckpt["series"])
            resume_skip_until_ts = float(_ckpt["last_completed_bar_ts"])
            _log(f"resumed from checkpoint  last_completed="
                 f"{_iso(pd.Timestamp(resume_skip_until_ts, unit='s', tz='UTC'))}  "
                 f"fills={len(fills)}  snapshots={len(series)}")
        except Exception as e:
            _log(f"checkpoint load failed ({type(e).__name__}: {e}); fresh start")
            ex = BacktestExchange(starting_usd=cfg.starting_usd)
            trader = BacktestTrader(ex)
            thinker_state = {
                c: bt_thinker.ThinkerState.fresh(len(TF_NAMES)) for c in active
            }
            cached_epoch_ts = {c: None for c in active}
            fills = []
            series = []
            resume_skip_until_ts = 0.0

    # ── Daemon watchdog ──────────────────────────────────────────────
    _progress = [_time.monotonic(), 0]  # [last_progress, bar_idx]
    _watchdog_stop = threading.Event()
    def _watchdog():
        while not _watchdog_stop.wait(15.0):
            gap = _time.monotonic() - _progress[0]
            if gap > 120.0:
                _log(f"WATCHDOG: no bar progress for {gap:.0f}s — "
                     f"stuck at bar {_progress[1]:,}/{len(all_idx):,}")
    threading.Thread(target=_watchdog, daemon=True).start()

    # ── Main loop ────────────────────────────────────────────────────
    _heartbeat_every = 1000
    _walk_t0 = _time.monotonic()
    _last_log_step = 0
    for step, T_pd in enumerate(all_idx):
        T = float(T_pd.timestamp())
        if T <= resume_skip_until_ts:
            bars_resumed += 1
            continue
        _progress[0] = _time.monotonic()
        _progress[1] = step

        for c in active:
            meta = coin_meta[c]
            if T_pd < meta["first_ts"]:
                continue
            try:
                row = meta["grid5"].loc[T_pd]
            except KeyError:
                continue   # coin has no bar at this exact T

            # Epoch swap if we've crossed the boundary
            ei = bisect.bisect_right(meta["epoch_starts"], T) - 1
            if ei < 0:
                continue
            ep_ts = meta["epoch_starts"][ei]
            if cached_epoch_ts[c] != ep_ts:
                cached_epoch_ts[c] = ep_ts
                td_path = (
                    ws.training_epoch_dir(run_id, ep_ts, c)
                    / "training_data.json"
                )
                if not td_path.exists():
                    parsed_td[c] = None
                else:
                    try:
                        td_all = json.loads(td_path.read_text())
                        parsed_td[c] = {
                            tf: bt_thinker.parse_tf_training_data(td_all.get(tf, {}))
                            for tf in TF_NAMES
                        }
                    except Exception:
                        parsed_td[c] = None
            if parsed_td[c] is None:
                continue

            live_price = float(row["open"])
            fill_price = float(row["close"])

            # Score, vote, rebuild (lifted verbatim from engine.py)
            st = thinker_state[c]
            new_high_tf = list(st.high_tf_prices)
            new_low_tf = list(st.low_tf_prices)
            new_perfects = list(st.perfects)
            if len(new_high_tf) != len(TF_NAMES):
                new_high_tf = [0.0] * len(TF_NAMES)
                new_low_tf = [0.0] * len(TF_NAMES)
                new_perfects = ["inactive"] * len(TF_NAMES)

            for tf_idx, (tf_name, tf_min) in enumerate(zip(TF_NAMES, TF_MINUTES)):
                tf_df = tf_frames[(c, tf_min)]
                if tf_df.empty:
                    continue
                bs_unix = _bar_start_for_tf(T, tf_min)
                bs_ts = pd.Timestamp(bs_unix, unit="s", tz="UTC")
                pos = tf_df.index.searchsorted(bs_ts, side="right") - 1
                if pos < 0:
                    continue
                bar_row = tf_df.iloc[pos]
                open_p = float(bar_row["open"])
                hd, ld, status = bt_thinker.score_tf(
                    parsed_td[c][tf_name], open_p, live_price,
                )
                ht, lt = bt_thinker.compute_tf_prices(live_price, hd, ld, status)
                new_high_tf[tf_idx] = ht
                new_low_tf[tf_idx] = lt
                new_perfects[tf_idx] = status

            long_count = 0
            short_count = 0
            for i in range(len(TF_NAMES)):
                hb = (st.high_bound_prices[i]
                      if i < len(st.high_bound_prices)
                      else 99999999999999999)
                lb = (st.low_bound_prices[i]
                      if i < len(st.low_bound_prices) else 0.0)
                vote = bt_thinker.vote_one(
                    live_price, hb, lb, new_high_tf[i], new_low_tf[i],
                )
                if vote == "long":
                    long_count += 1
                elif vote == "short":
                    short_count += 1

            high_bounds, low_bounds = bt_thinker.rebuild_bounds(
                new_high_tf, new_low_tf, new_perfects,
            )
            n_tfs = len(TF_NAMES)
            if len(high_bounds) < n_tfs:
                high_bounds = list(high_bounds) + [99999999999999999] * (n_tfs - len(high_bounds))
            if len(low_bounds) < n_tfs:
                low_bounds = list(low_bounds) + [0.0] * (n_tfs - len(low_bounds))

            uniq = {round(v, 12): v for v in low_bounds if v is not None}
            long_levels = sorted(uniq.values(), reverse=True)

            trader.set_signals(c, long_count, short_count, long_levels)
            ex.set_bar(c, live_price, fill_price)

            st.high_tf_prices = new_high_tf
            st.low_tf_prices = new_low_tf
            st.perfects = new_perfects
            st.high_bound_prices = high_bounds
            st.low_bound_prices = low_bounds

        # Tick the trader for ALL coins (shared cash pool)
        ex.set_time(T)
        trader.set_now(T)
        trader.manage_trades()

        new_orders = ex.orders_log()[len(fills):]
        for o in new_orders:
            fills.append(o)

        # Daily snapshot
        if step % cfg.snapshot_every_n == 0:
            tot_pos_val = 0.0
            snap = {"ts": T_pd, "ts_iso": _iso(T_pd), "cash": ex._cash}
            for c in active:
                qty = float(ex._holdings.get(c, 0.0) or 0.0)
                price = float(ex._bar_open_prices.get(c, 0.0) or 0.0)
                pos_val = qty * price
                tot_pos_val += pos_val
                snap[f"qty_{c}"] = qty
                snap[f"position_usd_{c}"] = pos_val
            snap["total_position_usd"] = tot_pos_val
            snap["total_account_value"] = ex._cash + tot_pos_val
            series.append(snap)

            # Per-snapshot checkpoint flush. Worst-case crash loses
            # at most one snapshot interval (default = 1 day).
            try:
                _save_checkpoint(
                    run_id, T,
                    ex.to_state(),
                    trader.to_state(),
                    {c: {
                        "high_tf_prices": list(s.high_tf_prices),
                        "low_tf_prices": list(s.low_tf_prices),
                        "high_bound_prices": list(s.high_bound_prices),
                        "low_bound_prices": list(s.low_bound_prices),
                        "perfects": list(s.perfects),
                    } for c, s in thinker_state.items()},
                    dict(cached_epoch_ts),
                    fills, series,
                )
            except Exception as _e:
                _log(f"checkpoint flush failed: {type(_e).__name__}: {_e}")

        # Heartbeat
        if step and step % _heartbeat_every == 0:
            _bars = step - _last_log_step
            _dt_sec = _time.monotonic() - _walk_t0
            _last_log_step = step
            _eta_s = _dt_sec * (len(all_idx) - step) / max(step, 1)
            _log(f"bar {step:,}/{len(all_idx):,} "
                 f"{_iso(T_pd)}  "
                 f"cash=${ex._cash:,.0f} "
                 f"total=${ex._cash + sum((ex._holdings.get(c,0.0) or 0.0) * (ex._bar_open_prices.get(c,0.0) or 0.0) for c in active):,.0f} "
                 f"fills={len(fills)}  "
                 f"eta={_eta_s/60:.1f}min")

    _watchdog_stop.set()

    _walk_total = _time.monotonic() - _walk_t0
    _log(f"walk done in {_walk_total:.1f}s "
         f"({len(all_idx)/max(_walk_total,1e-9):.0f} bars/s)")

    # Write outputs
    fills_df = pd.DataFrame(fills)
    series_df = pd.DataFrame(series)
    out_dir = ws.ensure_dir(ws.run_dir(run_id))
    if not fills_df.empty:
        fills_df.to_parquet(out_dir / "fills.parquet")
    if not series_df.empty:
        series_df.to_parquet(out_dir / "series.parquet")

    final_val = (
        float(series_df["total_account_value"].iloc[-1])
        if not series_df.empty else cfg.starting_usd
    )
    pct_return = (final_val / cfg.starting_usd - 1.0) * 100.0
    _log(f"final total_account_value=${final_val:,.2f}  "
         f"return={pct_return:+.2f}%  fills={len(fills_df)}  "
         f"snapshots={len(series_df)}")

    return PortfolioRunResult(
        fills=fills_df, series=series_df,
        coins_active=active, coins_skipped=skipped,
        bars_processed=len(all_idx),
        bars_resumed=bars_resumed,
    )
