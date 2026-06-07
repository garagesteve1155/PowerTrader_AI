"""
Reproduce a stuck bar from a watchdog dump, in-process, with no Ray.

Usage:
    python3 -m backtest.reproduce_stuck \
        backtest/runs/<run_id>/stuck/ADA_20260607_145649_epoch12_bar2168.json

What it does:
  1) Loads the watchdog dump JSON.
  2) Reconstructs BacktestExchange and BacktestTrader with the captured
     state at the moment the wedge began.
  3) Loads the epoch's training_data.json so thinker scoring is identical
     to the live run.
  4) Pulls the kucoin5 bar at the captured bar_ts and the per-TF candles
     needed for scoring.
  5) Runs ONE bar's worth of engine work inline, printing per-component
     timings and a `>>>` line for each significant boundary.

Add a pdb breakpoint manually around any phase to step into the wedge:

    import pdb; pdb.set_trace()
    trader.manage_trades()       # then `s` to step

This bypasses Ray entirely so all the usual debug tooling works.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

import pt_trader
from pt_env import TRAIN_TF_MINUTES, TRAIN_TF_NAMES
from pt_pricesource import ArcticPriceSource

from . import thinker as bt_thinker
from . import workspace as ws
from .exchange import BacktestExchange
from .trader import BacktestTrader


TF_MINUTES = list(TRAIN_TF_MINUTES)
TF_NAMES = list(TRAIN_TF_NAMES)


def _reconstruct_trader(coin: str, trader_dump: dict) -> BacktestTrader:
    """Build a BacktestTrader and load the captured state.

    The dump uses field names without leading underscores (e.g.
    'dca_buy_ts') while load_state expects the underscored attribute
    names — translate as we build the load_state dict.
    """
    ex_stub = BacktestExchange(starting_usd=0.0)
    trader = BacktestTrader(ex_stub)
    trader.load_state({
        "pnl_ledger":           trader_dump.get("pnl_ledger") or {},
        "cost_basis":           trader_dump.get("cost_basis") or {},
        "trailing_pm":          trader_dump.get("trailing_pm") or {},
        "dca_levels_triggered": trader_dump.get("dca_levels_triggered") or {},
        "_dca_buy_ts":          trader_dump.get("dca_buy_ts") or {},
        "_dca_last_sell_ts":    trader_dump.get("dca_last_sell_ts") or {},
        "_skipped_coins":       set(),
        "_skip_throttle":       {},
        "trailing_settings_sig": (
            float(trader_dump.get("trailing_gap_pct", 0.0)),
            float(trader_dump.get("pm_start_pct_no_dca", 0.0)),
            float(trader_dump.get("pm_start_pct_with_dca", 0.0)),
        ),
    })
    if trader_dump.get("trailing_gap_pct") is not None:
        trader.trailing_gap_pct = float(trader_dump["trailing_gap_pct"])
    if trader_dump.get("pm_start_pct_no_dca") is not None:
        trader.pm_start_pct_no_dca = float(trader_dump["pm_start_pct_no_dca"])
    if trader_dump.get("pm_start_pct_with_dca") is not None:
        trader.pm_start_pct_with_dca = float(trader_dump["pm_start_pct_with_dca"])
    return trader


def _reconstruct_exchange(exchange_dump: dict) -> BacktestExchange:
    """Build a BacktestExchange from the (partial) exchange snapshot.

    The dump captures cash, holdings, and orders_log SIZE only (not the
    full orders log). For replaying one bar that's fine — orders_log is
    only appended to during the bar; nothing reads it for decisions.
    """
    ex = BacktestExchange(starting_usd=float(exchange_dump.get("cash", 0.0)))
    ex._holdings = dict(exchange_dump.get("holdings") or {})
    ex._orders_log = []  # forensic-only, not consulted by decisions
    return ex


def _reconstruct_thinker_state(thinker_dump: dict) -> bt_thinker.ThinkerState:
    return bt_thinker.ThinkerState(
        high_tf_prices=list(thinker_dump.get("high_tf_prices") or []),
        low_tf_prices=list(thinker_dump.get("low_tf_prices") or []),
        high_bound_prices=list(thinker_dump.get("high_bound_prices") or []),
        low_bound_prices=list(thinker_dump.get("low_bound_prices") or []),
        perfects=list(thinker_dump.get("perfects") or []),
    )


def _bar_start_for_tf(t_unix: float, tf_minutes: int) -> int:
    secs = tf_minutes * 60
    return int(t_unix // secs) * secs


def run_one_bar(dump_path: Path, breakpoint_in: str | None = None) -> None:
    dump = json.loads(dump_path.read_text())
    coin = dump["coin"]
    run_id = dump["run_id"]
    bar_ts_str = dump["bar_ts"]
    live_price = float(dump["live_price"])
    fill_price = float(dump["fill_price"])
    epoch_start = dump.get("epoch_start")
    if epoch_start is None:
        sys.exit("dump missing epoch_start; can't locate training_data.json")

    print(f">>> dump:               {dump_path}")
    print(f">>> coin:               {coin}")
    print(f">>> run_id:             {run_id}")
    print(f">>> bar_ts:             {bar_ts_str}")
    print(f">>> live/fill price:    {live_price} / {fill_price}")
    print(f">>> wedged_in (dump):   {dump.get('wedged_in_component', '?')}  "
          f"({dump.get('component_elapsed_seconds', 0):.1f}s)")
    print(f">>> bar in epoch:       {dump['bar_in_epoch']} / 4032 (approx)")
    print(f">>> epoch start:        {epoch_start}")
    print()

    # Inject sweep params from the dump so the trader uses the same
    # decision thresholds as the live run.
    if dump.get("trader", {}).get("pm_start_pct_no_dca") is not None:
        pt_trader.PM_START_PCT_NO_DCA = float(dump["trader"]["pm_start_pct_no_dca"])
        pt_trader.PM_START_PCT_WITH_DCA = float(dump["trader"]["pm_start_pct_with_dca"])
    pt_trader.crypto_symbols = [coin]
    pt_trader.LONG_TERM_SYMBOLS = set()
    pt_trader.EXCLUDED_COINS = set()

    # Build state objects
    ex = _reconstruct_exchange(dump.get("exchange") or {})
    trader = _reconstruct_trader(coin, dump.get("trader") or {})
    trader.exchange = ex
    state = _reconstruct_thinker_state(dump.get("thinker") or {})

    print(f">>> exchange cash:      ${ex._cash:.4f}  holdings={ex._holdings}")
    pos = trader._pnl_ledger.get("open_positions", {}) or {}
    print(f">>> open_positions:     {list(pos.keys())}")
    print(f">>> trailing_pm:        {trader.trailing_pm}")
    print()

    # Load training_data for the epoch
    asof_ts = pd.Timestamp(epoch_start, tz="UTC").timestamp()
    td_path = (
        ws.training_epoch_dir(run_id, asof_ts, coin) / "training_data.json"
    )
    if not td_path.exists():
        sys.exit(f"training_data.json not found at {td_path}")
    td_all = json.loads(td_path.read_text())
    parsed = {
        tf: bt_thinker.parse_tf_training_data(td_all.get(tf, {}))
        for tf in TF_NAMES
    }
    print(f">>> training_data:      {td_path}")
    print(f">>> memory counts:      "
          + ", ".join(f"{tf}={len(parsed[tf].memory_list)}" for tf in TF_NAMES))
    print()

    # Load all TF candles
    src = ArcticPriceSource()
    tf_frames = {tf_min: src.get_candles(coin, tf_min) for tf_min in TF_MINUTES}

    # Find the stuck bar's timestamp object
    bar_ts_pd = pd.Timestamp(bar_ts_str)
    T = float(bar_ts_pd.timestamp())

    print(">>> running bar phases inline (will print per-component timings)")
    print()

    # ---- score ----
    t0 = time.monotonic()
    new_high_tf, new_low_tf, new_perfects = list(state.high_tf_prices), list(state.low_tf_prices), list(state.perfects)
    if len(new_high_tf) != len(TF_NAMES):
        new_high_tf = [0.0] * len(TF_NAMES)
        new_low_tf = [0.0] * len(TF_NAMES)
        new_perfects = ["inactive"] * len(TF_NAMES)
    for tf_idx, (tf_name, tf_min) in enumerate(zip(TF_NAMES, TF_MINUTES)):
        tf_df = tf_frames[tf_min]
        if tf_df.empty:
            continue
        bar_start_unix = _bar_start_for_tf(T, tf_min)
        bar_start_ts = pd.Timestamp(bar_start_unix, unit="s", tz="UTC")
        pos = tf_df.index.searchsorted(bar_start_ts, side="right") - 1
        if pos < 0:
            continue
        bar_row = tf_df.iloc[pos]
        open_p = float(bar_row["open"])
        hd, ld, status = bt_thinker.score_tf(parsed[tf_name], open_p, live_price)
        ht, lt = bt_thinker.compute_tf_prices(live_price, hd, ld, status)
        new_high_tf[tf_idx] = ht; new_low_tf[tf_idx] = lt; new_perfects[tf_idx] = status
    print(f"  score          {(time.monotonic() - t0)*1000:.3f}ms")
    if breakpoint_in == "score":
        breakpoint()

    # ---- vote ----
    t0 = time.monotonic()
    long_count = short_count = 0
    for i in range(len(TF_NAMES)):
        hb = state.high_bound_prices[i] if i < len(state.high_bound_prices) else 99999999999999999
        lb = state.low_bound_prices[i] if i < len(state.low_bound_prices) else 0.0
        v = bt_thinker.vote_one(live_price, hb, lb, new_high_tf[i], new_low_tf[i])
        long_count += (v == "long"); short_count += (v == "short")
    print(f"  vote           {(time.monotonic() - t0)*1000:.3f}ms   "
          f"long={long_count} short={short_count}")
    if breakpoint_in == "vote":
        breakpoint()

    # ---- rebuild ----
    t0 = time.monotonic()
    high_bounds, low_bounds = bt_thinker.rebuild_bounds(new_high_tf, new_low_tf, new_perfects)
    n_tfs = len(TF_NAMES)
    if len(high_bounds) < n_tfs:
        high_bounds = list(high_bounds) + [99999999999999999] * (n_tfs - len(high_bounds))
    if len(low_bounds) < n_tfs:
        low_bounds = list(low_bounds) + [0.0] * (n_tfs - len(low_bounds))
    print(f"  rebuild        {(time.monotonic() - t0)*1000:.3f}ms")
    if breakpoint_in == "rebuild":
        breakpoint()

    uniq = {round(v, 12): v for v in low_bounds if v is not None}
    long_levels = sorted(uniq.values(), reverse=True)

    # ---- signal ----
    t0 = time.monotonic()
    trader.set_signals(coin, long_count, short_count, long_levels)
    ex.set_bar(coin, live_price, fill_price)
    ex.set_time(T)
    trader.set_now(T)
    print(f"  signal         {(time.monotonic() - t0)*1000:.3f}ms")

    # ---- manage_trades ---- THE BIG ONE
    if breakpoint_in == "manage_trades":
        breakpoint()
    t0 = time.monotonic()
    trader.manage_trades()
    print(f"  manage_trades  {(time.monotonic() - t0)*1000:.3f}ms")

    # ---- capture ----
    t0 = time.monotonic()
    new_orders = ex.orders_log()
    print(f"  capture        {(time.monotonic() - t0)*1000:.3f}ms   "
          f"new_orders={len(new_orders)}")

    print()
    print(">>> bar replay complete")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dump_path", help="Path to a runs/<run_id>/stuck/<coin>_*.json")
    p.add_argument(
        "--break-in",
        choices=["score", "vote", "rebuild", "manage_trades"],
        default=None,
        help="Drop into pdb just before the named phase",
    )
    args = p.parse_args()
    run_one_bar(Path(args.dump_path), breakpoint_in=args.break_in)


if __name__ == "__main__":
    main()
