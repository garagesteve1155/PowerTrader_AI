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
from typing import List

import pandas as pd

from pt_pricesource import ArcticPriceSource

from . import workspace as ws
from . import aggregate as agg
from .engine import BacktestParams, CoinRunConfig, run_coin
from .sweep import _train_coin_phase, default_grid, run_coin_sweep
from .train import epoch_schedule, train_one_epoch


def cmd_pilot(args):
    coin = args.coin.upper()
    src = ArcticPriceSource()
    run_id = ws.new_run_id(prefix=f"pilot_{coin}")
    print(f"run_id = {run_id}")

    sched = list(epoch_schedule(coin, pd.Timestamp.utcnow().tz_localize("UTC"), src))
    if args.epochs:
        sched = sched[: args.epochs]
    if not sched:
        print(f"no viable epochs for {coin} (need 100 weekly bars)")
        return

    print(f"training {len(sched)} epochs: {sched[0].date()} → {sched[-1].date()}")
    t0 = time.time()
    for asof in sched:
        train_one_epoch(run_id, coin, asof.timestamp())
    print(f"training elapsed: {time.time()-t0:.1f}s")

    cfg = CoinRunConfig(
        coin=coin,
        starting_usd=args.starting_usd,
        until=None,
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
    until = pd.Timestamp.utcnow().tz_localize("UTC")
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
    pilot.add_argument("--coin", required=True)
    pilot.add_argument("--epochs", type=int, default=2)
    pilot.add_argument("--lvl", type=int, default=2)
    pilot.add_argument("--alloc", type=float, default=1.0)
    pilot.add_argument("--pm", type=float, default=4.0)
    pilot.add_argument("--starting-usd", type=float, default=1000.0)
    pilot.set_defaults(func=cmd_pilot)

    run = sub.add_parser("run", help="Full single-coin run, default params")
    run.add_argument("--coin", required=True)
    run.add_argument("--lvl", type=int, default=2)
    run.add_argument("--alloc", type=float, default=1.0)
    run.add_argument("--pm", type=float, default=4.0)
    run.add_argument("--starting-usd", type=float, default=1000.0)
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
