"""
Command-line driver for the PowerTrader backtest pipeline.

Pipeline overview
-----------------
Each invocation does some subset of:
  1) Training. For each coin × 14-day epoch in the schedule, invoke
     pt_trainer with asof_ts set so no post-epoch data leaks in. Output:
       runs/<run_id>/training/<YYYYMMDD>/<COIN>/training_data.json
  2) Replay. Walk the 5min kucoin5 grid, score the 7 trained TFs at each
     bar, vote, drive a BacktestTrader subclass of the prod Trader, and
     record fills/snapshots. Output:
       runs/<run_id>/fills/<COIN>.parquet
       runs/<run_id>/series/<COIN>.parquet
  3) Aggregation. Resample snapshots to hourly, sum to portfolio level,
     derive daily % returns. Output:
       runs/<run_id>/agg/<COIN>_hourly.parquet
       runs/<run_id>/agg/portfolio_hourly.parquet
       runs/<run_id>/agg/portfolio_daily.parquet

The runs/ directory is gitignored. All steps are resumable: if a
training_data.json or fills/series parquet already exists for a given
(run, coin, asof), it is reused and the step is skipped.

Subcommands
-----------
pilot
    Train + run a single coin (or list, or all configured coins) for a
    bounded number of 14-day epochs. The replay window is capped at the
    end of the last requested epoch so pilots stay short (~13s/epoch).
    Use this to validate end-to-end before launching a full run.

run
    Same as `pilot` but with no `--epochs` cap — replays from each coin's
    earliest viable date up to "now". Hours of compute for one coin's
    full ~6-year history; days for all 30 coins serially. Resume any
    interrupted run via --run-id.

sweep
    3D parameter sweep over (trade_start_level, start_allocation_pct,
    pm_start_pct). 350 param points × N coins. Trains each coin's
    epoch schedule once (param-independent), then runs an independent
    backtest per (coin, params) via Ray when available, serial fallback
    with --serial.

aggregate
    Build hourly + daily aggregations from existing fill/series parquets
    in a run. Run after pilot/run/sweep to populate the agg/ subdir that
    backtest/research.py reads.

Common options
--------------
--coin <symbol>
    Single coin (e.g. ETH).
--coin <a,b,c>
    Comma-separated list (e.g. BTC,ETH,SOL).
--coin (omitted on pilot/run)
    Default to every coin in pt_config.json. Multi-coin runs are serial,
    one coin at a time. Each coin gets its own $1000 starting capital and
    its own subtree under runs/<run_id>/.

--run-id <existing>
    Resume an existing run (pilot/run only). The trainer skips epochs
    whose training_data.json is already on disk; the engine skips epochs
    with no training_data.json. So passing the same --run-id picks up
    exactly where the previous invocation stopped.

--epochs N
    pilot only. Trains and replays the first N epochs of the schedule.
    Defaults to 2. Omit (use `run` instead) to do all epochs.

--lvl, --alloc, --pm
    Inline sweep parameters for pilot/run only. Defaults match the prod
    pt_config.json values (lvl=2, alloc=1%, pm=4%).

--starting-usd N
    Starting cash per coin. Default 1000.

--serial (sweep only)
    Disable Ray. Run all 350 param points sequentially. Useful when Ray
    isn't installed or for deterministic single-machine timing.

Examples
--------
Single-coin pilot, 2 epochs (~25s):
    python3 -m backtest.cli pilot --coin ETH --epochs 2

Full single-coin replay (one coin, all history):
    python3 -m backtest.cli run --coin ETH

Resume an interrupted run by re-issuing the command with --run-id:
    python3 -m backtest.cli run --coin ETH --run-id pilot_ETH_20260601_072651

Three coins, full history, into one run_id directory tree:
    python3 -m backtest.cli run --coin BTC,ETH,SOL

All 30 configured coins, full history:
    python3 -m backtest.cli run

3D sweep, one coin (350 backtests, Ray-parallel by default):
    python3 -m backtest.cli sweep --coin ETH

Aggregate after a multi-coin run:
    python3 -m backtest.cli aggregate <run_id> --coins BTC,ETH,SOL

Then explore the Marimo notebook:
    /home/dave/app/anaconda3/envs/dev/bin/marimo edit backtest/research.py

Output layout
-------------
runs/<run_id>/
  training/<YYYYMMDD>/<COIN>/training_data.json    # one per 14-day epoch
  training/<YYYYMMDD>/<COIN>/trainer_state.json
  fills/<COIN>.parquet                             # chronological fill log
  series/<COIN>.parquet                            # per-5min state snapshots
  agg/<COIN>_hourly.parquet                        # hourly per-coin
  agg/portfolio_hourly.parquet                     # hourly portfolio sum
  agg/portfolio_wide_hourly.parquet                # per-coin matrix
  agg/portfolio_daily.parquet                      # daily + daily_pct_return

Sweep sub-runs land in sibling directories named
runs/<run_id>__<COIN>__l<L>_a<A>_p<P>/. The Marimo notebook picks them
up automatically and renders a (lvl × alloc) heatmap averaged over pm.

Failure handling
----------------
- Each epoch's training is isolated. If pt_trainer crashes on a specific
  (coin, asof), the CLI prints `FAIL -- <error>` for that epoch and moves
  on to the next.
- The end-of-loop summary lists (trained, skipped, failed) counts and a
  block of every failed epoch with its error message.
- The engine skips epochs whose training_data.json doesn't exist
  (because training failed). The trader keeps using the previous epoch's
  training for that 14-day window.

Safety
------
- **Do not run two trainers against the same `--run-id` concurrently.**
  There is no file locking. Two processes writing to the same
  training_data.json or series parquet will corrupt it.
- The runs/ directory is gitignored; nothing under it should be committed.
- Prod state at /mnt/d/dave/Documents/powertrader/powertrader_demo/state/
  is read-only — never write there from the backtest.
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
from .train import epoch_schedule, train_grid, train_one_epoch


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


def cmd_train(args):
    """Train-only phase: produce training_data.json for every (coin × epoch).

    Independent across (coin, asof) tuples, so Ray-parallelizes cleanly.
    Subsequent `run` / `sweep` invocations with the same --run-id will
    pick up the cached training data via the skip-if-done logic.
    """
    coins = _resolve_coins(args.coin)
    if not coins:
        print("no coins to train")
        return

    src = ArcticPriceSource()
    run_id = args.run_id or ws.new_run_id(prefix="train")
    print(f"run_id = {run_id}{'  (resuming)' if args.run_id else ''}")
    print(f"coins ({len(coins)}): {', '.join(coins)}")

    now_utc = pd.Timestamp.utcnow()
    if now_utc.tz is None:
        now_utc = now_utc.tz_localize("UTC")

    t0 = time.time()
    by_coin = train_grid(
        run_id=run_id,
        coins=coins,
        until=now_utc,
        parallel=not args.serial,
        epochs_per_coin=args.epochs,
        price_source=src,
    )
    elapsed = time.time() - t0

    # Per-coin summary
    print(f"\nFinished in {elapsed:.1f}s "
          f"({'serial' if args.serial else 'Ray-parallel'})")
    failed_rows: list[tuple[str, str, str]] = []  # (coin, asof, error)
    grand_trained = grand_skipped = grand_failed = 0
    for coin, results in by_coin.items():
        n_skip = sum(1 for r in results if r.skipped)
        n_fail = sum(1 for r in results if not r.ok)
        n_train = len(results) - n_skip - n_fail
        grand_trained += n_train
        grand_skipped += n_skip
        grand_failed += n_fail
        print(f"  {coin:<5}  {n_train:>4} trained, "
              f"{n_skip:>4} skipped, {n_fail:>3} failed  "
              f"(of {len(results)} epochs)")
        for r in results:
            if not r.ok:
                failed_rows.append((coin, str(r.asof.date()), r.error or "?"))

    print(f"  TOTAL  {grand_trained:>4} trained, "
          f"{grand_skipped:>4} skipped, {grand_failed:>3} failed")

    if failed_rows:
        print(f"\n=== {len(failed_rows)} failed epoch(s) ===")
        for coin, asof_str, err in failed_rows:
            print(f"  {coin}  {asof_str}  {err}")


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

    train = sub.add_parser(
        "train",
        help="Produce all training_data.json artifacts (Ray-parallel across "
             "coin × epoch). Replay/sweep can then reuse via --run-id.",
    )
    train.add_argument("--coin", default=None,
                       help="Coin symbol, comma-separated list, "
                            "or omit for all pt_config.json coins")
    train.add_argument("--epochs", type=int, default=None,
                       help="Cap on epochs per coin (default: all viable)")
    train.add_argument("--run-id", default=None,
                       help="Existing run_id to resume (default: new timestamped)")
    train.add_argument("--serial", action="store_true",
                       help="Disable Ray; train coin × epoch sequentially")
    train.set_defaults(func=cmd_train)

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
