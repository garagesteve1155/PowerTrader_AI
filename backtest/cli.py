"""
Command-line driver for the PowerTrader backtest pipeline.

Pipeline overview
-----------------
The pipeline factors into three independent stages, all sharing one
`run_id` directory tree:

  1) Training. For each coin × 14-day epoch, invoke pt_trainer with
     asof_ts set so no post-epoch data leaks in. Param-independent —
     produced once, reused across every replay below.
       runs/<run_id>/training/<YYYYMMDD>/<COIN>/training_data.json
       runs/<run_id>/training/<YYYYMMDD>/<COIN>/trainer_state.json

  2) Replay. Walk the 5min kucoin5 grid, score the 7 trained TFs at
     each bar, vote, drive a BacktestTrader subclass of the prod
     Trader, and record fills + state snapshots. Per (coin, params).
       runs/<run_id>/fills/<COIN>.parquet
       runs/<run_id>/series/<COIN>.parquet

  3) Aggregation. Resample snapshots to hourly, sum to portfolio
     level, derive daily % returns.
       runs/<run_id>/agg/<COIN>_hourly.parquet
       runs/<run_id>/agg/portfolio_hourly.parquet
       runs/<run_id>/agg/portfolio_daily.parquet

The runs/ directory is gitignored. Every stage is resumable: if a
training_data.json or fills/series parquet already exists for a given
(run, coin, asof), it is reused and the step is skipped.

The recommended large-scale workflow is to run stages explicitly so the
expensive training phase is done once and shared:

    # Phase A — produce all training data, Ray-parallel
    python3 -m backtest.cli train

    # Phase B — default-params replay over the same training
    python3 -m backtest.cli run    --run-id <phase_a_run_id>

    # Phase C — 3D sweep over the same training
    python3 -m backtest.cli sweep  --run-id <phase_a_run_id>

`pilot` and `run` will also train missing epochs on the fly if you skip
Phase A, but their training is per-coin serial — slower than `train`.

Subcommands
-----------
train
    Produce training_data.json for every (coin × epoch). Ray-parallel
    across the full grid by default (one Ray task per epoch). No replay
    is performed. Use this as Phase A before launching `run` or `sweep`
    against the same --run-id so they reuse the cached training instead
    of doing it again per-coin.

pilot
    Train + run a single coin (or list, or all configured coins) for a
    bounded number of 14-day epochs. The replay window is capped at the
    end of the last requested epoch so pilots stay short (~13s/epoch).
    Use this to validate end-to-end before launching a full run. Training
    inside pilot is per-coin serial; pass --run-id to a previous `train`
    output to skip it entirely.

run
    Same as `pilot` but with no `--epochs` cap — replays from each coin's
    earliest viable date up to "now". Hours of compute for one coin's
    full ~6-year history; days for all 30 coins serially. Pass --run-id
    to resume or to reuse a `train` artifact.

sweep
    3D parameter sweep over (trade_start_level, start_allocation_pct,
    pm_start_pct). 350 param points × N coins. Each coin's training
    schedule is (re)materialised via train_grid (Ray-parallel across
    epochs) before fanning out a replay task per (coin, params) — also
    Ray-parallel. Pass --run-id to reuse an existing `train` output.
    `--serial` disables Ray for both phases.

aggregate
    Build hourly + daily aggregations from existing fill/series parquets
    in a run. Run after pilot/run/sweep to populate the agg/ subdir that
    backtest/research.py reads.

Options
-------
Which options each subcommand accepts:

  Option            train  pilot  run    sweep  aggregate
  ----------------  -----  -----  -----  -----  ---------
  --coin            opt    opt    opt    opt    --coins (plural, REQ)
  --epochs          yes    yes    --     --     --
  --lvl             --     yes    yes    --     --
  --alloc           --     yes    yes    --     --
  --pm              --     yes    yes    --     --
  --starting-usd    --     yes    yes    --     --
  --run-id          yes    yes    yes    --     --
  --serial          yes    yes    yes    yes    --
  run_id (positional)                            REQ

Legend: opt = optional, REQ = required, yes = accepted, -- = not accepted.

Note: `aggregate` takes the run_id as a *positional* argument (not
--run-id) and uses `--coins` (plural) for the coin list; every other
subcommand uses `--coin` (singular) accepting a single symbol, a
comma-separated list, or omitted (meaning every coin in pt_config.json).

Per-option semantics
~~~~~~~~~~~~~~~~~~~~

--coin <symbol> | <a,b,c> | omitted
    Single coin (e.g. ETH), comma-separated list (e.g. BTC,ETH,SOL),
    or omitted to default to every coin in pt_config.json. Each coin
    gets its own $1000 starting capital and its own subtree under
    runs/<run_id>/. train fans the coin × epoch grid across Ray; pilot
    and run fan out one Ray task per coin (replay within a coin is
    path-dependent and stays sequential across epochs).

--run-id <existing>
    Resume / reuse an existing run. The trainer skips epochs whose
    training_data.json is already on disk; the engine skips epochs with
    no training_data.json and resumes from the per-coin checkpoint
    pickle for replay state. Passing the same --run-id thus picks up
    exactly where the previous invocation stopped, or composes
    Phase A + Phase B.

--epochs N
    Cap the schedule to the first N 14-day epochs per coin.
    train: caps the training grid (default: all viable).
    pilot: caps both training and replay (default 2).
    Not accepted on `run` — `run` always does the full viable schedule;
    use `pilot --epochs N` for a bounded smoke run.

--lvl, --alloc, --pm
    Inline sweep parameters for pilot/run. Defaults match the prod
    pt_config.json values (lvl=2, alloc=1%, pm=4%).
    train doesn't take these because training is param-independent.
    sweep doesn't take them because the whole point of sweep is to
    vary them across the default 3D grid.

--starting-usd N
    Starting cash per coin (pilot/run only). Default 1000.

--serial
    Disable Ray; run every fan-out task sequentially. Useful when Ray
    isn't installed, when debugging a single task, or for deterministic
    timing. Accepted on train, pilot, run, sweep.

Positional / aggregate-only
    `aggregate <run_id> --coins X,Y,Z` reads the per-coin series/fills
    parquets that previous pilot/run/sweep invocations wrote, and
    produces the hourly + daily portfolio aggregates the Marimo notebook
    reads. --coins is required and accepts a comma-separated list only
    (no default-to-all).

Examples
--------
Phase A — train everything, Ray-parallel (≈ 25 min on 24 cores for the
full universe of 30 coins × ~173 epochs):
    python3 -m backtest.cli train

Phase A subset — train BTC + ETH + SOL only:
    python3 -m backtest.cli train --coin BTC,ETH,SOL

Phase A resume — re-launch with the same run-id; skip-if-done picks up:
    python3 -m backtest.cli train --run-id train_20260601_081535

Phase B — default-params replay reusing Phase A training:
    python3 -m backtest.cli run --run-id train_20260601_081535

Phase C — 3D sweep reusing Phase A training:
    python3 -m backtest.cli sweep --coin ETH --run-id train_20260601_081535

One-shot pilot for quick validation (2 epochs, single coin, ~25s):
    python3 -m backtest.cli pilot --coin ETH --epochs 2

One-shot single-coin full replay (does its own training inline):
    python3 -m backtest.cli run --coin ETH

Three coins, full history, into one run_id directory tree:
    python3 -m backtest.cli run --coin BTC,ETH,SOL

All 30 configured coins, full history:
    python3 -m backtest.cli run

Resume an interrupted run by re-issuing the command with --run-id:
    python3 -m backtest.cli run --coin ETH --run-id pilot_ETH_20260601_072651

Aggregate after a multi-coin run:
    python3 -m backtest.cli aggregate <run_id> --coins BTC,ETH,SOL

Then explore in the Marimo notebook:
    /home/dave/app/anaconda3/envs/dev/bin/marimo edit backtest/research.py

Output layout
-------------
runs/<run_id>/
  training/<YYYYMMDD>/<COIN>/training_data.json    # one per 14-day epoch
  training/<YYYYMMDD>/<COIN>/trainer_state.json
  fills/<COIN>.parquet                             # chronological fill log
  series/<COIN>.parquet                            # per-5min state snapshots
  checkpoint/<COIN>.pkl                            # resumable per-epoch state
  agg/<COIN>_hourly.parquet                        # hourly per-coin
  agg/portfolio_hourly.parquet                     # hourly portfolio sum
  agg/portfolio_wide_hourly.parquet                # per-coin matrix
  agg/portfolio_daily.parquet                      # daily + daily_pct_return
  report.jsonl                                     # live event stream
  report.txt                                       # human-readable summary

Sweep sub-runs land in sibling directories named
runs/<run_id>__<COIN>__l<L>_a<A>_p<P>/. The Marimo notebook picks them
up automatically and renders a (lvl × alloc) heatmap averaged over pm.

Monitoring a live run
---------------------
`run`, `pilot`, and `sweep` each write a timestamped event log to
`runs/<run_id>/report.jsonl` as work proceeds, plus a human-readable
`runs/<run_id>/report.txt` summary at end-of-run. Both paths are echoed
to the CLI's stdout on startup so you can copy them straight into a
second terminal.

Event types in report.jsonl
  run_started     subcommand, coins, params, run_id
  task_started    coin, pid                     (workers emit via stdout)
  task_completed  coin + every descriptive field listed below
  coin_started    (sweep) coin, index, total
  coin_completed  (sweep) coin, elapsed_s, n_param_points, n_ok, n_error
  task_error      coin, error
  run_completed   elapsed_s, n_ok, n_error

task_completed field reference
  schedule_first_epoch       first 14-day asof in the coin's schedule
                             (its earliest viable training date)
  schedule_last_epoch        most recent asof — i.e. now-aligned 14-day grid
  epochs_total               total number of 14-day epochs in the schedule
  epochs_replayed_total      lifetime epochs the engine has walked through
                             (this invocation + every prior resumed one)
  epochs_replayed_this_run   NEW epochs the engine walked in *this* call
  epochs_resumed             epochs already on disk from prior runs (these
                             contributed to epochs_replayed_total but were
                             not redone here)
  epochs_trained_this_run    epochs the trainer trained from scratch in
                             *this* call
  epochs_skipped_already_done epochs whose training_data.json was reused
  epochs_failed_this_run     epochs that crashed during training in this call
  fills_total                lifetime fill count across the coin's history
  snapshots_total            lifetime hourly snapshot count
  pct_return_total           total %-return from $1000 starting capital
                             across the full backtest (positive = profit)
  wall_seconds_replay        wall-clock time spent inside run_coin in this
                             call (not including the training phase)
  resumed_from               date of the checkpoint's last completed epoch,
                             or null if this was a fresh start
  replayed_through           date of the most recent bar processed (i.e.
                             where the run ended for this coin)
  error                      only on task_error: "ExceptionType: msg"

A nearly-no-op resume (everything already done) looks like:
  epochs_total = N, epochs_replayed_total = N, epochs_replayed_this_run = 0,
  epochs_trained_this_run = 0, epochs_skipped_already_done = N,
  resumed_from = replayed_through = last bar's date

Tail the raw JSONL stream:
  tail -f backtest/runs/<run_id>/report.jsonl

Tail a single-line summary per event (jq-style with stdlib):
  tail -f backtest/runs/<run_id>/report.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    coin = e.get('coin', '')
    extra = ''
    if e['event'] == 'task_completed':
        extra = f\"  elapsed={e['elapsed_s']:.1f}s  fills={e['fills']}  return={e['pct_return']:+.2f}%\"
    elif e['event'] == 'task_error':
        extra = f\"  ERROR: {e['error']}\"
    print(f\"{e['ts']}  {e['event']:<18}  {coin:<5}{extra}\", flush=True)
"

Per-worker progress beacons (per-epoch timing inside a coin's replay)
live in the Ray worker logs, NOT in report.jsonl. To watch the slow
worker(s) in real time:

  tail -f /tmp/ray/session_latest/logs/worker-*.out

Each log line is prefixed with `[worker pid=X coin=Y]` so you can grep
for one coin's full lifecycle:

  grep "coin=BTC" /tmp/ray/session_latest/logs/worker-*.out

What's currently working (live snapshot of active Ray workers)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
To see exactly which coin each live worker is processing and how far
it has got — one short line per active worker — paste this into a
second terminal while a run is in flight:

  for pid in $(pgrep -f "ray::_pilot_worker"); do
    last=$(grep -h "pid=$pid" /tmp/ray/session_latest/logs/worker-*.out 2>/dev/null | tail -1)
    cpu=$(ps -p "$pid" -o pcpu= 2>/dev/null | tr -d ' ')
    printf "pid=%-7s cpu=%5s%%  %s\n" "$pid" "$cpu" "$last"
  done

Healthy workers print a heartbeat every ~0.85 seconds; a stuck worker
shows the SAME last-log-line each time you re-run the snippet. A
worker at 0% CPU with no recent log line is either idle (between
tasks) or dead.

For a continuous live view (refresh every 2 seconds):

  watch -n 2 'for pid in $(pgrep -f "ray::_pilot_worker"); do
    last=$(grep -h "pid=$pid" /tmp/ray/session_latest/logs/worker-*.out 2>/dev/null | tail -1)
    cpu=$(ps -p "$pid" -o pcpu= 2>/dev/null | tr -d " ")
    printf "pid=%-7s cpu=%5s%%  %s\n" "$pid" "$cpu" "$last"
  done'

For just the coins (drop pid/cpu, list distinct coins currently
running):

  for pid in $(pgrep -f "ray::_pilot_worker"); do
    grep -h "pid=$pid" /tmp/ray/session_latest/logs/worker-*.out 2>/dev/null \
      | tail -1
  done | grep -oE "coin=[A-Z]+" | sort -u

If a worker hangs, use the `pid=` field from its last log line to find
it in `ps aux | grep "ray::_pilot_worker"`, then check that worker's
.out file for the last completed epoch.

Aggregate progress quickly during a multi-coin run by counting
checkpoints written (one per completed (coin, epoch) within a coin):

  ls backtest/runs/<run_id>/checkpoint/ | wc -l    # number of coins done
  ls backtest/runs/<run_id>/training/      | wc -l # number of epoch dirs

Or jump to the final summary the moment the run finishes:

  cat backtest/runs/<run_id>/report.txt

Failure handling
----------------
- Each epoch's training is isolated. If pt_trainer crashes on a specific
  (coin, asof), the CLI prints `FAIL -- <error>` for that epoch and moves
  on to the next. Per-coin and total trained/skipped/failed counts are
  reported at the end, followed by a block listing every failed epoch
  with its error.
- pt_trainer's failure paths use `sys.exit(1)`. train_one_epoch catches
  the resulting SystemExit and returns ok=False instead of aborting the
  whole CLI (Ctrl-C / KeyboardInterrupt still propagates normally).
- The engine skips epochs whose training_data.json doesn't exist
  (because training failed). The trader keeps using the previous epoch's
  training for that 14-day window.

Parallelism
-----------
- `train` and `sweep` fan out over Ray when it's installed. Each Ray
  worker is its own OS process with its own CWD, so the chdir inside
  train_one_epoch can't collide between concurrent epochs.
- The ArcticDB lmdb store is read-only here and supports unlimited
  concurrent readers across processes.
- Within a single process, training is serial (Ray runs each task in
  its own worker).

Safety
------
- **Do not run two trainers against the same `--run-id` concurrently.**
  There is no file locking. Two processes writing to the same
  training_data.json or series parquet will corrupt it. Resume *across*
  separate sequential invocations is fine — that's serialised by the
  process boundary plus the skip-if-done check.
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
from . import report as rpt
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

    if len(coins) == 1:
        _cmd_pilot_one(args)
        return

    # Multi-coin: each coin is an independent path-dependent backtest.
    # Fan out across coins via Ray when available. Within a coin, replay
    # stays internally sequential (path-dependent across epochs).
    serial = bool(getattr(args, "serial", False))
    run_id = args.run_id or ws.new_run_id(prefix="pilot")
    print(f"running {len(coins)} coins: {', '.join(coins)}"
          f"   (run_id = {run_id}{'  resuming' if args.run_id else ''})")
    print(f"report: backtest/runs/{run_id}/report.jsonl  (tail -f to monitor)")
    cfg_dict = {
        "run_id": run_id,
        "lvl": args.lvl,
        "alloc": float(args.alloc),
        "pm": float(args.pm),
        "starting_usd": float(args.starting_usd),
        "epochs": args.epochs,
    }

    rpt.event(
        run_id, "run_started",
        subcommand=("run" if args.epochs is None else "pilot"),
        coins=coins,
        params={"lvl": args.lvl, "alloc": float(args.alloc),
                "pm": float(args.pm), "starting_usd": float(args.starting_usd),
                "epochs": args.epochs},
    )
    _run_t0 = time.monotonic()

    if not serial:
        try:
            import ray  # type: ignore
        except ImportError:
            print("[cmd_pilot] Ray not installed — falling back to serial")
            serial = True

    results: list[dict] = []
    if serial:
        for c in coins:
            print(f"\n── {c} ──")
            args.coin = c
            args.run_id = run_id
            _cmd_pilot_one(args)
            # Serial path doesn't return a structured dict; emit a
            # placeholder completion event with what we know.
            rpt.event(run_id, "task_completed_serial", coin=c)
    else:
        import ray  # type: ignore
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, log_to_driver=False)
        remote = ray.remote(_pilot_worker)
        futures = [remote.remote(c, cfg_dict) for c in coins]
        # Collect results as they finish so the JSONL stays live during
        # the run (tail -f will see completions arrive in real time).
        pending = list(futures)
        while pending:
            done, pending = ray.wait(pending, num_returns=1)
            try:
                r = ray.get(done[0])
            except Exception as e:
                rpt.event(run_id, "task_error",
                          coin="?", error=f"{type(e).__name__}: {e}")
                continue
            results.append(r)
            if r.get("error"):
                rpt.event(run_id, "task_error",
                          coin=r["coin"], error=r["error"])
            else:
                # Forward every descriptive field from _pilot_worker.
                # Keeping a single forwarding loop avoids missing fields
                # when new ones are added in the worker.
                event_payload = {k: v for k, v in r.items() if k != "error"}
                rpt.event(run_id, "task_completed", **event_payload)

    n_ok = sum(1 for r in results if not r.get("error"))
    n_err = len(results) - n_ok
    rpt.event(run_id, "run_completed",
              elapsed_s=time.monotonic() - _run_t0,
              n_ok=n_ok, n_error=n_err)
    rpt.write_summary_text(run_id)

    print("\n=== per-coin summary ===")
    for r in results:
        if r.get("error"):
            print(f"  {r['coin']:<5}  ERROR: {r['error']}")
        else:
            print(
                f"  {r['coin']:<5}  "
                f"epochs total={r['epochs_total']:>3}  "
                f"replayed total={r['epochs_replayed_total']:>3}  "
                f"this-run replay={r['epochs_replayed_this_run']:>3}  "
                f"trained={r['epochs_trained_this_run']:>3}  "
                f"fills={r['fills_total']:>4}  "
                f"return={r['pct_return_total']:+.2f}%"
            )
    print(f"\nreport: backtest/runs/{run_id}/report.txt")


def _pilot_worker(coin: str, cfg: dict) -> dict:
    """Top-level picklable worker for one coin. Returns a small summary dict.

    Each Ray worker is its own process. The pt_trader globals it mutates
    here are local to the worker.
    """
    import time as _time
    import pandas as _pd
    import pt_trader as _pt
    from .train import epoch_schedule as _epoch_schedule
    from .train import train_one_epoch as _train_one_epoch
    from .engine import (
        BacktestParams as _BP, CoinRunConfig as _Cfg, run_coin as _run_coin,
    )

    import os as _os
    pid = _os.getpid()
    tag = f"[worker pid={pid} coin={coin.upper()}]"
    # All log lines flushed immediately so Ray's per-worker .out captures
    # them even if the worker hangs later — essential for diagnosing the
    # "which coin is that stuck PID running?" case.
    def _log(msg: str) -> None:
        print(f"{tag} {msg}", flush=True)

    _log("starting")
    out = {"coin": coin.upper(), "error": None}
    try:
        _pt.TRADE_START_LEVEL = int(cfg["lvl"])
        _pt.START_ALLOC_PCT = float(cfg["alloc"])
        _pt.PM_START_PCT_NO_DCA = float(cfg["pm"])
        _pt.PM_START_PCT_WITH_DCA = float(cfg["pm"])
        _pt.crypto_symbols = [coin.upper()]
        _pt.LONG_TERM_SYMBOLS = set()
        _pt.EXCLUDED_COINS = set()

        src = ArcticPriceSource()
        now_utc = _pd.Timestamp.utcnow()
        if now_utc.tz is None:
            now_utc = now_utc.tz_localize("UTC")
        sched = list(_epoch_schedule(coin, now_utc, src))
        if cfg.get("epochs"):
            sched = sched[: cfg["epochs"]]
        _log(f"schedule resolved: {len(sched)} epochs")
        if not sched:
            out["error"] = "no viable epochs"
            _log("EXIT no_viable_epochs")
            return out

        _log(f"training phase begin: {sched[0].date()} → {sched[-1].date()}")
        train_t0 = _time.monotonic()
        n_skip = n_fail = 0
        for asof in sched:
            ed = ws.training_epoch_dir(cfg["run_id"], asof.timestamp(), coin)
            existed = (ed / "training_data.json").exists()
            r = _train_one_epoch(cfg["run_id"], coin, asof.timestamp())
            if existed and r.ok:
                n_skip += 1
            elif not r.ok:
                n_fail += 1
        # Descriptive event-payload fields.
        out["epochs_trained_this_run"] = len(sched) - n_skip - n_fail
        out["epochs_skipped_already_done"] = n_skip
        out["epochs_failed_this_run"] = n_fail
        out["epochs_total"] = len(sched)
        out["schedule_first_epoch"] = sched[0].strftime("%Y-%m-%d")
        out["schedule_last_epoch"] = sched[-1].strftime("%Y-%m-%d")
        _log(f"training phase done in {_time.monotonic()-train_t0:.1f}s "
             f"(trained={out['epochs_trained_this_run']} "
             f"skipped={n_skip} failed={n_fail})")

        until = (
            min(sched[-1] + _pd.Timedelta(days=14), now_utc)
            if cfg.get("epochs") else None
        )
        rc = _Cfg(
            coin=coin.upper(),
            starting_usd=float(cfg["starting_usd"]),
            until=until,
            record_every_n=12,
            params=_BP(
                trade_start_level=int(cfg["lvl"]),
                start_allocation_pct=float(cfg["alloc"]),
                pm_start_pct=float(cfg["pm"]),
            ),
        )
        _log(f"replay phase begin: {len(sched)} epoch(s)")
        engine_t0 = _time.monotonic()
        res = _run_coin(cfg["run_id"], rc, epoch_schedule=sched, price_source=src)
        out["wall_seconds_replay"] = _time.monotonic() - engine_t0
        out["epochs_replayed_this_run"] = res.epochs_used
        out["epochs_replayed_total"] = res.epochs_used + res.epochs_resumed
        out["epochs_resumed"] = res.epochs_resumed
        out["fills_total"] = len(res.fills)
        out["snapshots_total"] = len(res.series)
        out["replayed_through"] = res.replayed_through
        out["resumed_from"] = res.resumed_from
        if res.series is not None and len(res.series):
            first = float(res.series.iloc[0]["total_account_value"])
            last = float(res.series.iloc[-1]["total_account_value"])
            out["pct_return_total"] = (last / first - 1.0) * 100.0 if first else 0.0
        else:
            out["pct_return_total"] = 0.0
        _log(f"EXIT ok  replay_elapsed={out['wall_seconds_replay']:.1f}s  "
             f"epochs_replayed_this_run={out['epochs_replayed_this_run']}  "
             f"fills_total={out['fills_total']}  "
             f"return={out['pct_return_total']:+.2f}%")
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        _log(f"EXIT error  {out['error']}")
        return out


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
    coins = _resolve_coins(args.coin)
    if not coins:
        print("no coins to sweep")
        return

    # Run-id naming: include coin if just one, otherwise generic
    if len(coins) == 1:
        run_id = ws.new_run_id(prefix=f"sweep_{coins[0]}")
    else:
        run_id = ws.new_run_id(prefix="sweep")
    print(f"sweep run_id = {run_id}")
    print(f"report: backtest/runs/{run_id}/report.jsonl  (tail -f to monitor)")

    until = pd.Timestamp.utcnow()
    if until.tz is None:
        until = until.tz_localize("UTC")
    grid = default_grid()
    total_tasks = len(grid) * len(coins)
    print(f"grid size: {len(grid)} param points × {len(coins)} coin(s) = "
          f"{total_tasks} backtests  "
          f"({'parallel' if not args.serial else 'serial'})")
    print(f"coins: {', '.join(coins)}")

    rpt.event(
        run_id, "run_started",
        subcommand="sweep",
        coins=coins,
        params={"grid_size": len(grid), "total_tasks": total_tasks,
                "serial": bool(args.serial)},
    )
    _sweep_t0 = time.monotonic()

    # Each coin's sweep is internally Ray-parallel across its 350 param
    # points. Per-coin training (param-independent) happens once before
    # that coin's fan-out. Multiple coins are dispatched sequentially —
    # one coin's Ray cluster cycle at a time — to keep memory pressure
    # bounded.
    all_results = []
    for ci, coin in enumerate(coins, 1):
        print(f"\n── sweep [{ci}/{len(coins)}] {coin} ──")
        _coin_t0 = time.monotonic()
        rpt.event(run_id, "coin_started", coin=coin, index=ci, total=len(coins))
        rs = run_coin_sweep(
            run_id, coin, until=until, grid=grid, parallel=not args.serial,
        )
        all_results.extend(rs)
        n_ok = sum(1 for r in rs if not r.error)
        n_err = sum(1 for r in rs if r.error)
        rpt.event(
            run_id, "coin_completed",
            coin=coin, elapsed_s=time.monotonic() - _coin_t0,
            n_param_points=len(rs), n_ok=n_ok, n_error=n_err,
            avg_fills=(sum(r.rows_fills for r in rs if not r.error)
                       / max(n_ok, 1)),
        )
        for r in rs:
            if r.error:
                rpt.event(run_id, "task_error",
                          coin=r.coin, params=str(r.params), error=r.error)

    n_ok = sum(1 for r in all_results if not r.error)
    n_err = sum(1 for r in all_results if r.error)
    rpt.event(run_id, "run_completed",
              elapsed_s=time.time() - _sweep_t0,
              n_ok=n_ok, n_error=n_err)
    rpt.write_summary_text(run_id)

    print(f"\ncompleted {len(all_results)} tasks across {len(coins)} coin(s)")
    errors = [r for r in all_results if r.error]
    ok = [r for r in all_results if not r.error]
    if errors:
        print(f"errors: {len(errors)}")
        for e in errors[:5]:
            print(f"  {e.coin} {e.params}: {e.error}")
    print(f"ok: {len(ok)}; avg fills/run: "
          f"{sum(r.rows_fills for r in ok)/max(len(ok),1):.1f}")
    print(f"\nreport: backtest/runs/{run_id}/report.txt")


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
    pilot.add_argument("--serial", action="store_true",
                       help="Disable Ray; run coins sequentially")
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
    run.add_argument("--serial", action="store_true",
                     help="Disable Ray; run coins sequentially")
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
    sweep.add_argument("--coin", default=None,
                       help="Coin symbol, comma-separated list, "
                            "or omit for all pt_config.json coins")
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
