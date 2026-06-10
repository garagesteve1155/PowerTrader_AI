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
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
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
    # When set, training_data.json is read from runs/<training_run_id>/
    # instead of runs/<run_id>/. Lets a sweep share one trained universe
    # across all its sub-runs.
    training_run_id: Optional[str] = None


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
    params_sig: Optional[tuple] = None,
) -> None:
    """Atomic write of full joint-engine state.

    params_sig is the (trade_start_level, start_allocation_pct,
    pm_start_pct) tuple of the run that produced this checkpoint.
    Loaded back via _load_checkpoint and compared at startup so a
    `pilot`/`run` invocation can't silently resume a checkpoint
    from a different param combo (different combos → different
    state trajectory, so resuming would be a correctness bug).
    """
    payload = {
        "version": _CHECKPOINT_VERSION,
        "last_completed_bar_ts": float(last_bar_ts),
        "params_sig": params_sig,
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

    # Sweep sub-runs read training from a shared parent; standalone
    # runs train into and read from their own run_id directory.
    training_id = cfg.training_run_id or run_id

    tag = f"[portfolio pid={os.getpid()}]"

    def _log(msg: str) -> None:
        print(f"{tag} {_iso(_dt.datetime.now(_dt.timezone.utc))} {msg}",
              flush=True)

    # ── Memory instrumentation ────────────────────────────────────
    # Lightweight RSS reader for diagnostic logging — no dependency
    # on psutil. /proc/self/status's VmRSS is the resident set size
    # in KiB (Linux/WSL). Returns 0.0 on platforms without it.
    def _rss_mib() -> float:
        try:
            with open(f"/proc/{os.getpid()}/status") as _f:
                for _line in _f:
                    if _line.startswith("VmRSS:"):
                        return float(_line.split()[1]) / 1024.0
        except Exception:
            pass
        return 0.0

    _log(f"startup rss={_rss_mib():.0f} MiB")

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
        # Phase 2b: precompute numpy column arrays for the master kucoin5
        # grid. Eliminates ~88s of pandas `.loc[T]` overhead per month
        # from the hot loop in favour of int-indexed numpy reads.
        #
        # Lookup-by-ns is via np.searchsorted on grid5_ts_ns rather than
        # a Python dict — kucoin5 indices are monotonically sorted so a
        # binary search is O(log N) at ~1 µs per call AND uses ~0 extra
        # memory. The old grid5_idx_by_ns dict held 525k Python int keys
        # + 525k Python int values + dict overhead = ~50 MiB per coin,
        # which on 28 coins was a flat ~1.5 GiB of pure dead weight.
        g5_ts_ns = grid5.index.values.astype("datetime64[ns]").view("i8")
        coin_meta[c] = {
            "sched": sched,
            "epoch_starts": [s.timestamp() for s in sched],
            "first_ts": grid5.index[0],
            "first_ts_ns": int(g5_ts_ns[0]),
            # int64 ns timestamps — used both for the master-grid union
            # below and for the hot-loop searchsorted lookup. The
            # original DataFrame is freed.
            "grid5_ts_ns": g5_ts_ns,
            "grid5_lows":   grid5["low"].values.astype(np.float64),
            "grid5_highs":  grid5["high"].values.astype(np.float64),
            "grid5_closes": grid5["close"].values.astype(np.float64),
        }
        del grid5

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
    _log(f"loaded {len(tf_frames)} TF candle frames  rss={_rss_mib():.0f} MiB")

    # Phase 2b: pre-extract int64-ns timestamp arrays + float64 open
    # arrays per (coin, tf). The hot loop uses np.searchsorted over the
    # int array (~5× faster than pandas Index.searchsorted) and an
    # int-indexed numpy read in place of `df.iloc[pos]["open"]` (which
    # used to box a full Series per call).
    tf_ts_ns: Dict[tuple, np.ndarray] = {}
    tf_opens: Dict[tuple, np.ndarray] = {}
    for (c, tf_min), df in tf_frames.items():
        if df.empty:
            tf_ts_ns[(c, tf_min)] = np.empty(0, dtype=np.int64)
            tf_opens[(c, tf_min)] = np.empty(0, dtype=np.float64)
            continue
        tf_ts_ns[(c, tf_min)] = df.index.values.astype("datetime64[ns]").view("i8")
        tf_opens[(c, tf_min)] = df["open"].values.astype(np.float64)

    # Free the per-TF DataFrames now that their numpy projections are
    # in tf_ts_ns / tf_opens. tf_frames was ~3 GiB for a 5y × 28-coin
    # × 7-TF load; the hot loop only touches the numpy arrays. The
    # per-coin grid5 DataFrames have already been freed above (line ~234,
    # `del grid5` after we extracted the int64 ts array + low/high/close
    # numpy views into coin_meta[c]["grid5_*"]).
    tf_frames.clear()
    import gc as _gc
    _gc.collect()

    # ── Master timeline = union of all coins' kucoin5 indices ────────
    # Old path: sorted(set().union(*indices)) — materialised ~14M Python
    #   pd.Timestamp objects (~1.1 GiB) + a ~1 GiB Python set + a sorted
    #   Python list. Peak ~3-4 GiB just to build a 4 MB int64 array.
    # New path: np.concatenate + np.unique on the already-extracted i8
    #   nanosecond arrays — pure C, ~200 MB peak (one i8 array of ~14M
    #   entries before unique; ~6M after dedupe = ~50 MB).
    _t0 = _time.monotonic()
    _ns_concat = np.concatenate(
        [coin_meta[c]["grid5_ts_ns"] for c in active]
    )
    _ns_unique = np.unique(_ns_concat)  # sorted + deduplicated in C
    del _ns_concat
    all_idx = pd.DatetimeIndex(_ns_unique.view("datetime64[ns]"), tz="UTC")
    del _ns_unique
    if cfg.from_date is not None:
        all_idx = all_idx[all_idx >= cfg.from_date]
    if cfg.until is not None:
        all_idx = all_idx[all_idx <= cfg.until]
    _log(f"master grid: {len(all_idx):,} bars  "
         f"{_iso(all_idx[0])} → {_iso(all_idx[-1])}  "
         f"(built in {_time.monotonic() - _t0:.1f}s)  "
         f"rss={_rss_mib():.0f} MiB")

    # ── Trader / exchange / per-coin thinker state ───────────────────
    pt_trader.TRADE_START_LEVEL = int(cfg.params.trade_start_level)
    pt_trader.START_ALLOC_PCT = float(cfg.params.start_allocation_pct)
    pt_trader.PM_START_PCT_NO_DCA = float(cfg.params.pm_start_pct)
    pt_trader.PM_START_PCT_WITH_DCA = float(cfg.params.pm_start_pct)
    pt_trader.crypto_symbols = list(active)
    pt_trader.EXCLUDED_COINS = set()
    # LTH inherits from prod pt_config.json (already loaded into
    # pt_trader.LONG_TERM_SYMBOLS at import time). Leave it.

    # `manage_trades` calls `_refresh_paths_and_symbols()` every tick to
    # hot-reload prod GUI changes. That overwrites the param overrides
    # we just set with whatever's in pt_config.json. Neutralise it for
    # the lifetime of this process — sweep param differentiation is
    # impossible otherwise.
    pt_trader._refresh_paths_and_symbols = lambda: None

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
    # Param signature for this run; compared to the checkpoint's stored
    # signature so we never silently resume a checkpoint produced by a
    # different (lvl, alloc, pm) combo.
    _cur_params_sig = (
        int(cfg.params.trade_start_level),
        float(cfg.params.start_allocation_pct),
        float(cfg.params.pm_start_pct),
    )
    if _ckpt is not None:
        _ckpt_sig = _ckpt.get("params_sig")
        if _ckpt_sig is not None and tuple(_ckpt_sig) != _cur_params_sig:
            _log(f"checkpoint params {tuple(_ckpt_sig)} != current "
                 f"{_cur_params_sig}; ignoring stale checkpoint and "
                 f"starting fresh")
            _ckpt = None
        elif _ckpt_sig is None:
            # Legacy checkpoint without a sig — could be any params. Refuse
            # to resume rather than risk a state-trajectory mismatch.
            _log("checkpoint has no params_sig (pre-fix); ignoring it and "
                 "starting fresh")
            _ckpt = None
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

    # ── Numba JIT warmup ──────────────────────────────────────────────
    # Even with @njit(cache=True), a fresh worker process pays per-process
    # LLVM init + cache-load cost on the first call to each njit function.
    # For the 4 thinker.py kernels (score_tf, compute_tf_prices,
    # rebuild_bounds, vote_one) that's ~30-40 s × 4 of CPU and a
    # multi-GB transient peak from LLVM IR + code-gen buffers — all
    # happening synchronously inside what would otherwise be bar 0,
    # tripping the 120-s watchdog and blowing through the Ray OOM-monitor
    # ceiling on parallel sweeps.
    #
    # Call each kernel here with a tiny representative input so all four
    # compilations happen in a controlled phase that we log and time.
    # The hot loop then hits already-warm functions.
    _warm_t0 = _time.monotonic()
    _warm_rss0 = _rss_mib()
    try:
        # Build a one-row ParsedTFMemory and a 1-element bound state.
        _warm_td = bt_thinker.parse_tf_training_data({
            "memories": "0.0{}0.0{}0.0",
            "weights": "1.0",
            "weights_high": "1.0",
            "weights_low": "1.0",
            "threshold": 1.0,
        })
        # 1 — score_tf
        _hd, _ld, _st = bt_thinker.score_tf(_warm_td, 100.0, 100.5)
        # 2 — compute_tf_prices
        _ht, _lt = bt_thinker.compute_tf_prices(100.0, _hd, _ld, _st)
        # 3 — rebuild_bounds (needs at least 2 TFs to exercise the
        #     gap-walk and original-order restoration)
        _hb, _lb = bt_thinker.rebuild_bounds(
            [100.5, 100.6], [99.5, 99.4], ["active", "active"],
        )
        # 4 — vote_one
        _v = bt_thinker.vote_one(100.0, _hb[0], _lb[0], 100.5, 99.5)
        _log(f"numba JIT warmup done in {_time.monotonic() - _warm_t0:.1f}s  "
             f"rss {_warm_rss0:.0f} → {_rss_mib():.0f} MiB")
    except Exception as _e:
        # Non-fatal — if warmup fails for any reason, the main loop will
        # still trigger compilation on bar 0 (the old behaviour).
        _log(f"numba JIT warmup failed ({type(_e).__name__}: {_e}); "
             f"first bar will compile inline")

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
    # Sliding window of (processed_bars, monotonic_time) over the last
    # 100 heartbeats = last ~100,000 processed bars. ETA derived from
    # the window gives a far more accurate projection in full-history
    # runs: per-bar cost grows over time as more coins come online, so
    # a global average underestimates the time still to come.
    _rate_window: deque = deque(maxlen=100)
    _rate_window.append((0, _walk_t0))

    # ── Resume fast-path ──────────────────────────────────────────────
    # Old behaviour: enumerate(all_idx) from the start, `continue` for
    # every already-walked bar. For a resume at e.g. bar 20,737 that's
    # 20,737 Python-loop iterations doing nothing — ~140s on this box,
    # tripping the 120s watchdog the whole way. Worse, _progress[0] only
    # updates inside the loop body PAST the continue, so the watchdog
    # has no signal that work is happening.
    #
    # Fast-path: searchsorted to find the first bar AFTER the resume
    # cutoff, then iterate from there directly. The skip-scan becomes
    # O(log N), measured in microseconds.
    if resume_skip_until_ts > 0:
        _resume_pd = pd.Timestamp(resume_skip_until_ts, unit="s", tz="UTC")
        _start_pos = int(all_idx.searchsorted(_resume_pd, side="right"))
        bars_resumed = _start_pos
        _walk_iter = enumerate(all_idx[_start_pos:], start=_start_pos)
    else:
        _walk_iter = enumerate(all_idx)
    for step, T_pd in _walk_iter:
        T = float(T_pd.timestamp())
        _progress[0] = _time.monotonic()
        _progress[1] = step

        # T's int64 nanosecond value, cached for the per-coin binary search.
        T_ns = T_pd.value

        for c in active:
            meta = coin_meta[c]
            if T_ns < meta["first_ts_ns"]:
                continue
            # np.searchsorted exact-match: side='left' gives the insertion
            # point for T_ns; if the value at that index matches, that's
            # this coin's row index. Otherwise this T isn't a bar boundary
            # for this coin and we skip.
            ts_arr = meta["grid5_ts_ns"]
            idx = int(ts_arr.searchsorted(T_ns, side="left"))
            if idx >= len(ts_arr) or ts_arr[idx] != T_ns:
                continue   # coin has no bar at this exact T

            # Epoch swap if we've crossed the boundary
            ei = bisect.bisect_right(meta["epoch_starts"], T) - 1
            if ei < 0:
                continue
            ep_ts = meta["epoch_starts"][ei]
            if cached_epoch_ts[c] != ep_ts:
                cached_epoch_ts[c] = ep_ts
                td_path = (
                    ws.training_epoch_dir(training_id, ep_ts, c)
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

            # Asymmetric decision-price model — mirrors what a live trader
            # ticking continuously inside a 5-min bar would have seen:
            #   buy decisions  ← bar LOW   (the dip that triggers entry/DCA)
            #   sell decisions ← bar HIGH × (1-trailing_gap_pct/100)
            #   fill / MTM     ← bar CLOSE (realistic execution after signal)
            # No look-ahead: LOW and HIGH are both within this bar's window.
            bar_low = float(meta["grid5_lows"][idx])
            bar_high = float(meta["grid5_highs"][idx])
            bar_close = float(meta["grid5_closes"][idx])
            _gap = float(getattr(pt_trader, "TRAILING_GAP_PCT", 0.5) or 0.5) / 100.0
            buy_price = bar_low
            sell_price = bar_high * (1.0 - _gap)
            fill_price = bar_close
            # The TF voting code uses `live_price` as the trader's current
            # view. Use the buy-decision price so "long" votes fire on the
            # dip — matches the live trader's continuous-tick perspective.
            live_price = buy_price

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
                ts_arr = tf_ts_ns[(c, tf_min)]
                if ts_arr.size == 0:
                    continue
                # Phase 2b: int-ns searchsorted + int-indexed numpy read.
                # Old code rebuilt a pd.Timestamp + boxed a Series per call.
                bs_ns = _bar_start_for_tf(T, tf_min) * 1_000_000_000
                pos = int(np.searchsorted(ts_arr, bs_ns, side="right")) - 1
                if pos < 0:
                    continue
                open_p = float(tf_opens[(c, tf_min)][pos])
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
            ex.set_bar(c, buy_price, sell_price, fill_price)

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
            rejects = ex.drain_dca_rejects()
            for c in active:
                qty = float(ex._holdings.get(c, 0.0) or 0.0)
                price = float(ex._fill_prices.get(c, 0.0) or 0.0)
                pos_val = qty * price
                tot_pos_val += pos_val
                snap[f"qty_{c}"] = qty
                snap[f"position_usd_{c}"] = pos_val
                r = rejects.get(c) or {}
                snap[f"dca_rejects_{c}_no_price"] = int(r.get("no_price", 0))
                snap[f"dca_rejects_{c}_zero_amount"] = int(r.get("zero_amount", 0))
                snap[f"dca_rejects_{c}_no_cash"] = int(r.get("no_cash", 0))
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
                    params_sig=_cur_params_sig,
                )
            except Exception as _e:
                _log(f"checkpoint flush failed: {type(_e).__name__}: {_e}")

        # Heartbeat. Counts only PROCESSED bars (post-resume) so the
        # rate/eta are honest even when most early bars were just
        # resume-skipped.
        processed = step - bars_resumed
        if processed and processed % _heartbeat_every == 0 and processed != _last_log_step:
            _now = _time.monotonic()
            _last_log_step = processed
            _rate_window.append((processed, _now))
            remaining = (len(all_idx) - bars_resumed) - processed
            # ETA from the sliding window — anchor = oldest entry, which
            # is at most 100 heartbeats (~100k bars) behind.
            _anchor_p, _anchor_t = _rate_window[0]
            _wp = processed - _anchor_p
            _wt = _now - _anchor_t
            if _wp > 0 and _wt > 0:
                _eta_s = _wt * remaining / _wp
            else:
                # Falls back to global rate if window is degenerate
                # (first heartbeat).
                _eta_s = (_now - _walk_t0) * remaining / max(processed, 1)
            _log(f"bar {processed:,}/{len(all_idx) - bars_resumed:,} "
                 f"{_iso(T_pd)}  "
                 f"cash=${ex._cash:,.0f} "
                 f"total=${ex._cash + sum((ex._holdings.get(c,0.0) or 0.0) * (ex._fill_prices.get(c,0.0) or 0.0) for c in active):,.0f} "
                 f"fills={len(fills)}  "
                 f"rss={_rss_mib():.0f} MiB  "
                 f"eta={_eta_s/60:.1f}min")

    _watchdog_stop.set()

    _walk_total = _time.monotonic() - _walk_t0
    _walk_bars = len(all_idx) - bars_resumed
    _log(f"walk done in {_walk_total:.1f}s "
         f"({_walk_bars/max(_walk_total,1e-9):.0f} bars/s; "
         f"{bars_resumed:,} resumed)")

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
