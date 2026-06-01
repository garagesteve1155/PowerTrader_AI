"""
Command-line driver for the backtest pipeline.

Subcommands:
  pilot       Train + run a single coin for a short window (validation).
  run         Train + run a single coin over full history with default params.
  sweep       3D parameter sweep on one or more coins.
  aggregate   Build hourly/daily series from existing run artifacts.

Examples:
  python3 -m backtest.cli pilot   --coin ETH --epochs 2
  python3 -m backtest.cli run     --coin ETH
  python3 -m backtest.cli sweep   --coin ETH
  python3 -m backtest.cli aggregate <run_id> --coins ETH

Run IDs land under backtest/runs/<run_id>/. The runs dir is gitignored.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

import pandas as pd

from pt_pricesource import ArcticPriceSource

from . import workspace as ws
from . import aggregate as agg
from .engine import BacktestParams, CoinRunConfig, run_coin
from .sweep import _train_coin_phase, default_grid, run_coin_sweep
from .train import epoch_schedule, train_one_epoch


def _resolve_coins(arg_value: Optional[str]) -> list[str]:
    """Parse --coin: comma-separated list, or all configured coins if blank."""
    if arg_value:
        return [c.strip().upper() for c in arg_value.split(",") if c.strip()]
    # Default to pt_config.json coin universe
    import pt_trader  # triggers config load
    return [c.upper() for c in pt_trader.crypto_symbols]


def cmd_pilot(args):
    coins = _resolve_coins(args.coin)
    if not coins:
        print("no coins to run")
        return

    if len(coins) > 1:
        print(f"running {len(coins)} coins: {', '.join(coins)}")
        for c in coins:
            args.coin = c
            _cmd_pilot_one(args)
            print()
        return
    _cmd_pilot_one(args)


def _cmd_pilot_one(args):
    coin = args.coin.upper()
    src = ArcticPriceSource()
    run_id = args.run_id or ws.new_run_id(prefix=f"pilot_{coin}")
    print(f"[{coin}] run_id = {run_id}{'  (resuming)' if args.run_id else ''}")

    now_utc = pd.Timestamp.utcnow()
    if now_utc.tz is None:
        now_utc = now_utc.tz_localize("UTC")
    sched = list(epoch_schedule(coin, now_utc, src))
    if args.epochs:
        sched = sched[: args.epochs]
    if not sched:
        print(f"no viable epochs for {coin} (need 100 weekly bars)")
        return

    print(f"training {len(sched)} epochs: {sched[0].date()} → {sched[-1].date()}")
    t0 = time.time()
    results = []
    n_skipped = 0
    for i, asof in enumerate(sched, 1):
        epoch_dir = ws.training_epoch_dir(run_id, asof.timestamp(), coin)
        was_done = (epoch_dir / "training_data.json").exists()
        res = train_one_epoch(run_id, coin, asof.timestamp())
        results.append(res)
        if was_done and res.ok:
            n_skipped += 1
        status = "skip" if (was_done and res.ok) else ("ok" if res.ok else "FAIL")
        print(f"  [{i}/{len(sched)}] {asof.date()}  {status}"
              + (f"  -- {res.error}" if not res.ok else ""))
    n_failed = sum(1 for r in results if not r.ok)
    n_trained = len(results) - n_skipped - n_failed
    print(f"training elapsed: {time.time()-t0:.1f}s  "
          f"({n_trained} trained, {n_skipped} skipped, {n_failed} failed)")
    if n_failed:
        print(f"\n=== {n_failed} epoch(s) failed ===")
        for r in results:
            if not r.ok:
                print(f"  {r.asof.date()}  {r.error}")

    # Bound `until` to the end of the last requested epoch so pilots stay short.
    if args.epochs:
        epoch_end = sched[-1] + pd.Timedelta(days=14)
        until = min(epoch_end, now_utc)
    else:
        until = None

    cfg = CoinRunConfig(
        coin=coin,
        starting_usd=args.starting_usd,
        until=until,
        record_every_n=12,
        params=BacktestParams(
            trade_start_level=args.lvl,
            start_allocation_pct=float(args.alloc),
            pm_start_pct=float(args.pm),
        ),
    )

    print(f"running engine across {len(sched)} epochs ({len(sched)*14} days)...")
    t0 = time.time()
    out = run_coin(run_id, cfg, epoch_schedule=sched, price_source=src)
    print(f"engine elapsed: {time.time()-t0:.1f}s")
    print(f"epochs_used={out.epochs_used}, fills={len(out.fills)}, snapshots={len(out.series)}")

    hourly = agg.write_per_coin_hourly(run_id, coin)
    if hourly is not None and not hourly.empty:
        first, last = hourly.iloc[0], hourly.iloc[-1]
        print(
            f"\nAccount: ${first['total_account_value']:.2f} → ${last['total_account_value']:.2f}  "
            f"({last['pct_return']:+.2f}%)"
        )


def cmd_run(args):
    args.epochs = None
    cmd_pilot(args)


def cmd_sweep(args):
    coin = args.coin.upper()
    run_id = ws.new_run_id(prefix=f"sweep_{coin}")
    print(f"sweep run_id = {run_id}")
    until = pd.Timestamp.utcnow()
    if until.tz is None:
        until = until.tz_localize("UTC")
    grid = default_grid()
    print(f"grid size: {len(grid)} param points × coin '{coin}'  ({'parallel' if not args.serial else 'serial'})")

    results = run_coin_sweep(
        run_id, coin, until=until, grid=grid, parallel=not args.serial,
    )
    print(f"completed {len(results)} tasks")
    errors = [r for r in results if r.error]
    if errors:
        print(f"errors: {len(errors)}")
        for e in errors[:5]:
            print(f"  {e.coin} {e.params}: {e.error}")
    ok = [r for r in results if not r.error]
    print(f"ok: {len(ok)}; avg fills/run: {sum(r.rows_fills for r in ok)/max(len(ok),1):.1f}")


def cmd_aggregate(args):
    run_id = args.run_id
    coins = [c.upper() for c in args.coins.split(",")]
    print(f"aggregating run_id={run_id}, coins={coins}")
    portfolio = agg.portfolio_hourly(run_id, coins)
    daily = agg.portfolio_daily(run_id, portfolio)
    print(f"portfolio_hourly rows: {len(portfolio)}, daily rows: {len(daily)}")


def main():
    p = argparse.ArgumentParser(prog="backtest")
    sub = p.add_subparsers(dest="cmd", required=True)

    pilot = sub.add_parser("pilot", help="Small validation run")
    pilot.add_argument("--coin", default=None,
                       help="Coin symbol, comma-separated list, "
                            "or omit for all pt_config.json coins")
    pilot.add_argument("--epochs", type=int, default=2)
    pilot.add_argument("--lvl", type=int, default=2)
    pilot.add_argument("--alloc", type=float, default=1.0)
    pilot.add_argument("--pm", type=float, default=4.0)
    pilot.add_argument("--starting-usd", type=float, default=1000.0)
    pilot.add_argument("--run-id", default=None,
                       help="Existing run_id to resume (default: new timestamped)")
    pilot.set_defaults(func=cmd_pilot)

    run = sub.add_parser("run", help="Full single-coin run, default params")
    run.add_argument("--coin", default=None,
                     help="Coin symbol, comma-separated list, "
                          "or omit for all pt_config.json coins")
    run.add_argument("--lvl", type=int, default=2)
    run.add_argument("--alloc", type=float, default=1.0)
    run.add_argument("--pm", type=float, default=4.0)
    run.add_argument("--starting-usd", type=float, default=1000.0)
    run.add_argument("--run-id", default=None,
                     help="Existing run_id to resume (default: new timestamped)")
    run.set_defaults(func=cmd_run)

    sweep = sub.add_parser("sweep", help="3D parameter sweep on one coin")
    sweep.add_argument("--coin", required=True)
    sweep.add_argument("--serial", action="store_true",
                       help="Disable Ray; run param points serially")
    sweep.set_defaults(func=cmd_sweep)

    aggr = sub.add_parser("aggregate", help="Aggregate per-coin -> portfolio")
    aggr.add_argument("run_id")
    aggr.add_argument("--coins", required=True, help="Comma-separated coin list")
    aggr.set_defaults(func=cmd_aggregate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
