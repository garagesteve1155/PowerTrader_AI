"""
Command-line driver for the PowerTrader joint multi-coin backtest.

Pipeline
--------
The pipeline is two stages, sharing one `run_id` directory tree:

  1) Training. For each coin × 14-day epoch, invoke pt_trainer with
     `asof_ts` set so no post-epoch data leaks in. Param-independent —
     produced once, reused across every replay below.
       runs/<run_id>/training/<YYYYMMDD>/<COIN>/training_data.json
       runs/<run_id>/training/<YYYYMMDD>/<COIN>/trainer_state.json

  2) Joint replay. ONE shared cash pool walks every coin's 5min kucoin5
     grid in lockstep, scoring all 7 trained TFs per coin per bar,
     letting the prod Trader logic open / DCA / close across the full
     universe. Cash is finite — coins compete for it.
       runs/<run_id>/fills.parquet         multi-coin fill log
       runs/<run_id>/series.parquet        daily portfolio snapshots
       runs/<run_id>/portfolio_daily.parquet   derived attribution
       runs/<run_id>/portfolio_checkpoint.pkl  resumable engine state

Both stages are resumable: if `training_data.json` exists for a given
(run_id, coin, asof) it is reused; if a `portfolio_checkpoint.pkl`
exists, the replay engine restarts from the last snapshot boundary.

Subcommands
-----------
train
    Produce `training_data.json` artifacts for the coin × epoch grid.
    Ray-parallel by default (one Ray task per epoch). No replay
    happens here. Use this as Phase A before launching `run` against
    the same `--run-id` so the replay can reuse the cached training.

pilot
    Joint backtest defaulting to the last ~6 months — minutes of
    wall-clock, not hours. Use to validate end-to-end before a full
    `run`. Requires `--run-id` of a prior `train` run (it does NOT
    train inline; coins lacking training_data.json are silently
    skipped per epoch).

run
    Joint backtest over the full viable history (each coin enters
    once it has 100 weekly candles + a trained TF). Hours of compute
    for the default 28-coin universe. Requires `--run-id` of a prior
    `train` run; same `--run-id` on a later invocation resumes from
    the most recent snapshot.

sweep
    3D parameter sweep over `(trade_start_level, start_allocation_pct,
    pm_start_pct)`. Requires `--run-id` of a prior `train` run whose
    training tree is shared across every sweep sub-run. Each grid
    point becomes one Ray task that calls the joint engine over the
    full coin universe. Default grid is 3×3×3 = 27 points; override
    with `--lvls`, `--allocs`, `--pms`. Per-point outputs land in
    `runs/<parent>/sweep/l<L>_a<A>_p<P>/`; rollup metrics in
    `runs/<parent>/sweep_results.parquet` and the long-format daily
    timeseries in `runs/<parent>/sweep_daily.parquet`.

aggregate
    Re-derive `portfolio_daily.parquet` from an existing run's
    `fills.parquet` + `series.parquet`. Only needed if you want to
    rebuild attribution after editing `portfolio_aggregate.py` —
    `run`/`pilot` already produce it inline.

Common options
--------------
--coin            Single symbol (BTC), comma-separated list
                  (BTC,ETH,SOL), or omitted to default to every coin
                  in `pt_config.json`. Same shape on every subcommand.

--run-id          The shared dir under runs/ for everything in this
                  pipeline.
                    train: omit for a fresh timestamped id; reuse
                      to resume (skips epochs already on disk).
                    run/pilot/sweep: REQUIRED, must point at a prior
                      train run_id. Replay outputs (fills, series,
                      portfolio_daily, checkpoint) land beside the
                      training tree in the same dir. Re-issuing
                      run/pilot with the same id resumes from the
                      engine checkpoint.

--serial          Disable Ray; serialise the inner fan-out. Accepted
                  on `train` (parallelism is per epoch) and `sweep`
                  (parallelism is per param point). `run`/`pilot`
                  walk one shared portfolio path-dependently, so
                  there is no parallelism to disable.

--max-parallel    (`sweep` only) Cap concurrent sub-runs in Ray mode.
                  Each sub-run is a full multi-coin joint backtest
                  with ~3-5 GiB steady-state resident for a 5-year ×
                  28-coin window after the engine memory fixes
                  (np.unique master-grid build, DataFrames freed
                  post-extraction).

                  Default = min(8, num_cpus, (MemAvailable − 4 GiB) / 6 GiB),
                  bounded to [1, 16]. A 32 GiB box with 26 GiB free
                  picks 3-4 workers; a 64 GiB box picks 8 (capped).
                  Shorter windows (`--from-date` recent) lower the
                  per-worker peak so bumping the cap once you've
                  shrunk the window is fine — `--max-parallel`
                  overrides the auto-detect.

                  Implementation belt-and-suspenders: `num_cpus` is
                  passed to `ray.init` AND the submission loop runs a
                  sliding window so at most `max_parallel` futures
                  exist concurrently. Each remote task is registered
                  with `max_calls=1`, forcing a fresh Python process
                  per sub-run so pandas / arctic / fills buffers can't
                  accumulate across tasks. Ray's Plasma object store
                  is capped at 2 GiB (vs. its default ~30 % of RAM).

                  The chosen value + measured MemAvailable are logged
                  on startup and emitted as a `sweep_concurrency`
                  event on `report.jsonl`.

                  Pass `--max-parallel 1` for a memory-test single
                  worker, or set it higher (e.g. `12`) on a beefier
                  Linux box with no WSL ceiling.

Joint-replay-only options
~~~~~~~~~~~~~~~~~~~~~~~~~
--lvl, --alloc, --pm
                  Trader knobs that drive the joint replay. Defaults
                  match prod (lvl=2, alloc=1%, pm=4%).
--starting-usd    Initial cash for the shared wallet. Default 10000.
--from-date       ISO date (YYYY-MM-DD) — earliest snapshot the
                  engine considers. Omit on `run` for "earliest
                  viable per coin"; `pilot` defaults to ~6 months ago.
--until-date      ISO date — latest snapshot. Defaults to now.

Train-only options
~~~~~~~~~~~~~~~~~~
--epochs N        Cap the training grid to the first N epochs per
                  coin. Default: all viable epochs.

Aggregate-only options
~~~~~~~~~~~~~~~~~~~~~~
run_id            Positional, OR pass --run-id. The run whose
                  fills/series to re-aggregate.

Examples
--------
Phase A — train every coin × epoch, Ray-parallel:
    python3 -m backtest.cli train

Phase B — full-history joint replay reusing Phase A training:
    python3 -m backtest.cli run --run-id train_20260601_084911

Quick smoke (~6 months, three coins, ~2 min):
    python3 -m backtest.cli pilot --coin BTC,ETH,SOL

Inline 3-month custom window:
    python3 -m backtest.cli run --coin BTC,ETH,SOL \\
        --from-date 2025-12-01 --until-date 2026-03-01

Phase C — 3D sweep reusing Phase A training (27 points × full universe):
    python3 -m backtest.cli sweep --run-id train_20260601_084911

Custom sweep grid (8 points, 3 coins, last 6 months):
    python3 -m backtest.cli sweep --run-id train_20260601_084911 \\
        --coin BTC,ETH,SOL --lvls 1,2 --allocs 0.5,2.0 --pms 4.0,8.0 \\
        --from-date 2025-12-01

Resume an interrupted joint run (same --run-id picks up from the
last snapshot):
    python3 -m backtest.cli run --run-id train_20260601_084911

Re-derive attribution after editing portfolio_aggregate.py:
    python3 -m backtest.cli aggregate portfolio_20260607_191833

Explore in the Marimo notebook:
    /home/dave/app/anaconda3/envs/dev/bin/marimo edit backtest/portfolio_research.py

Monitoring a live run
---------------------
Every run writes JSONL events to `runs/<run_id>/report.jsonl` plus a
human-readable `report.txt` summary at end-of-run. Both paths are
echoed on startup so you can `tail -f` from a second terminal.

The joint engine writes a per-snapshot heartbeat to its own log and
a daemon watchdog kills the process if no progress is made for 120s.

Monitoring a sweep
------------------
A sweep launches one joint backtest per grid point as a Ray task. Three
ways to watch it:

1) `tail -f runs/<parent>/report.jsonl` — the driver writes a
   `sweep_progress` event after every sub-run completes, with the
   running `n_done/n_total`, the grid coords (lvl/alloc/pm), the
   sub-run's pct_return, fill count, and any error string. Drains
   one-at-a-time via `ray.wait`, so the feed updates as workers
   finish (not all at the end).

2) Worker engine heartbeats stream to the driver terminal. Ray is
   initialised with `log_to_driver=True` so each worker's per-1000-bar
   heartbeats (bar count, cash, total, ETA) interleave on stdout.
   Lines are prefixed with the worker PID so you can demux by point.
   Noisy with 27+ points — pipe through `grep <pid>` or `tee`.

3) File-system completion check (works from anywhere, no Ray needed):
       PARENT=<parent_run_id>
       watch -n 15 'ls backtest/runs/'$PARENT'/sweep/*/series.parquet \
                       2>/dev/null | wc -l'
   `series.parquet` only appears when a sub-run finishes; the count is
   "X of grid_size done". `portfolio_checkpoint.pkl`'s mtime is the
   "this sub-run is still alive" signal for in-flight points.

At end-of-sweep the driver prints the top 5 by `pct_return`, the rollup
parquet path, and writes a final `run_completed` event.

Sweep memory & concurrency
~~~~~~~~~~~~~~~~~~~~~~~~~~
Each Ray task is a full multi-coin joint backtest: it loads kucoin5 +
7 trained TF grids for every coin, plus accumulating fills/series and
pandas / arctic / pyarrow caches. Steady-state is 2–4 GiB per worker
for a 5-year × 28-coin run; spikes go higher under GC pressure. With
16 logical CPUs spawning concurrently, that's enough to exhaust WSL2's
default 50 %-of-host memory ceiling and cascade into swap-thrashing.

Four backstops layered in defence-in-depth:

a) `ray.init(num_cpus=max_parallel)` caps the Ray scheduler pool.
b) The submission loop runs a sliding window of `max_parallel` futures
   in flight, so Ray's own queue doesn't accumulate either.
c) The remote function is registered with `max_calls=1`, forcing a
   fresh Python process per sub-run. This is the most impactful fix
   against creeping memory: pandas frame caches, pyarrow buffers, and
   the fills/series lists all get released by the OS when the worker
   exits between tasks. Costs ~1 s fork overhead per sub-run, which
   is invisible against 5–30 minutes of real work.
d) Ray's Plasma object store is capped at 2 GiB (vs. its default
   ~30 % of RAM = ~10 GiB on a 32 GiB box). The sub-run results are
   tiny dicts — Plasma doesn't need GiBs of headroom.

Default `max_parallel` comes from `_auto_max_parallel()`:
  cpu_cap  = min(8, num_cpus)
  mem_cap  = floor((MemAvailable − 4 GiB driver reserve) / 14 GiB)
  result   = max(1, min(cpu_cap, mem_cap, 16))

Per-worker budget (14 GiB) is empirical — both workers in a
`--max-parallel 2` run grew to ~12.7 GiB each within 12 s of startup,
*before either walk began*, just from loading 196 TF DataFrames and
materialising the union timestamp index. A 32 GiB WSL2 ceiling
therefore cannot host >1 worker on a 5y × 28-coin window. The mem_cap
formula reflects this.

Override with `--max-parallel N` if you know your box. On startup the
driver prints the chosen value, measured `MemAvailable`, per-worker
reserve, Plasma cap, and `max_calls=1`. The same data lands as a
`sweep_concurrency` event in `report.jsonl`.

Monitoring during a sweep:
   watch -n 5 'free -g; echo; ps aux --sort=-rss | head -10'

If `Swap` ever climbs above ~1 GiB during the run, kill and re-launch
with a lower `--max-parallel` (or raise `_PER_WORKER_GB` in cli.py if
you want the auto-detect to be more conservative). Sub-runs are
checkpointed inside each sub-run dir, so re-launching with the same
`--run-id` resumes each grid point from its last snapshot.

Spotting Ray-killed workers
---------------------------
A spike-then-drop pattern in `free -g` during a sweep is almost always
Ray's OOM monitor killing a sub-run that breached the per-worker budget
(or Linux's OOM-killer doing the same one layer down). The symptom in
`report.jsonl`:

    {"event": "sweep_progress", ..., "lvl": 3, "alloc": 2.0, "pm": 7.5,
     "pct_return": null, "error": "ray.get failed: OutOfMemoryError: ..."}

Or you'll see `OutOfMemoryError` in the worker log line just before
the spike resolves. Ray's default behaviour is to retry the failed
task 3× — which here is dangerous because the same param combo would
keep dying the same way. The sweep launcher overrides this with
`max_retries=0`: a killed grid point gets reported once with an error
field, the slot is freed for the next combo, and you re-run that
specific point later (see below).

Victim vs. offender — Ray's memory monitor at the *node* level kills
the most recently scheduled task when total node RAM exceeds 95 %.
That victim is NOT necessarily the actual memory hog. A typical OOM
message looks like:

    Memory on the node was 29.74GB / 31.22GB (0.952), which exceeds
    the memory usage threshold of 0.95. Ray killed this worker
    (pid=72341, memory used=3.13GB) because it was the most recently
    scheduled task.

The killed worker only had 3.13 GiB resident — but the *other*
worker concurrently running with `--max-parallel ≥ 2` had ballooned
to ~25 GiB and pushed the node over the threshold. The error row
in `sweep_results.parquet` correctly tags the victim's combo (we
track futures → params), but to find the actual offender you have
to isolate: re-run the suspect subset with `--max-parallel 1`.
With one worker, victim and offender are the same combo.

Some param combos (typically aggressive ones: low `lvl`, high
`alloc`, high `pm`) generate many more fills + DCA window entries
than conservative ones, accumulating multi-GiB of trader state per
sub-run. If `--max-parallel 1` still OOMs on a specific combo,
that's a real signal — the engine can't fit that combo in WSL's
memory ceiling and you either need to shorten the window
(`--from-date`) or bump WSL memory in `.wslconfig`.

Resuming an interrupted sweep
-----------------------------
Re-launch the exact same command with the same `--run-id`. The CLI
pre-flights every grid point against disk: any sub-run with both
`series.parquet` and `portfolio_daily.parquet` written is treated as
done, its row is reconstructed by reading the parquet directly (no
worker spawn, no re-walking), and a `sweep_progress` event flagged
`"from_cache": true` is emitted.

A `sweep_resume` event logs the split:

    {"event": "sweep_resume", "n_cached": 47, "n_to_run": 28,
     "n_total": 75}

So a sweep that died at ~60 % gets ~60 % of its work back for free
on re-launch; only the unfinished + previously-OOM-killed points run.
Workers that crashed mid-walk also resume from their per-sub-run
`portfolio_checkpoint.pkl`, so even those get partial credit.

If a specific param combo keeps dying after retries, that's the
clearest signal `--max-parallel` is still too high — drop it to 1
and let the offender run alone.

Output layout
-------------
runs/<run_id>/
  training/<YYYYMMDD>/<COIN>/training_data.json   # one per 14-day epoch
  training/<YYYYMMDD>/<COIN>/trainer_state.json
  fills.parquet                                   # joint multi-coin fills
  series.parquet                                  # daily portfolio snapshots
  portfolio_daily.parquet                         # derived attribution
  portfolio_checkpoint.pkl                        # resumable engine state
  report.jsonl                                    # live event stream
  report.txt                                      # end-of-run summary

Safety
------
- Do not run two writers against the same --run-id concurrently.
  There is no file locking; concurrent writes corrupt parquet/pickle.
  Sequential resume across separate invocations is fine.
- The `runs/` directory is gitignored — nothing under it should be
  committed.
- Prod state at /mnt/d/dave/Documents/powertrader/powertrader_demo/state/
  is read-only — never write there from the backtest.
"""

from __future__ import annotations

import argparse
import time
from typing import Optional

import pandas as pd

from pt_pricesource import ArcticPriceSource

from . import portfolio_aggregate as pa
from . import report as rpt
from . import workspace as ws
from .portfolio_engine import (
    PortfolioParams, PortfolioRunConfig, run_portfolio,
)
from .train import train_grid


def _resolve_coins(arg_value: Optional[str]) -> list[str]:
    """Parse --coin: comma-separated list, or all configured coins if blank."""
    if arg_value:
        return [c.strip().upper() for c in arg_value.split(",") if c.strip()]
    import pt_trader  # triggers config load
    return [c.upper() for c in pt_trader.crypto_symbols]


def _parse_iso_date(s: Optional[str]) -> Optional[pd.Timestamp]:
    if not s:
        return None
    ts = pd.Timestamp(s)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts


def _utcnow() -> pd.Timestamp:
    ts = pd.Timestamp.utcnow()
    return ts if ts.tz is not None else ts.tz_localize("UTC")


# ---------------------------------------------------------------------------
# Joint replay (run + pilot share this driver)
# ---------------------------------------------------------------------------

def _cmd_portfolio(args, default_from: Optional[pd.Timestamp]) -> None:
    """Shared driver for `run` and `pilot`. `pilot` differs only in the
    `default_from` it passes — `run` lets the engine pick each coin's
    earliest viable date."""
    coins = _resolve_coins(args.coin)
    if not coins:
        print("no coins to run")
        return

    run_id = args.run_id
    training_run_id = args.training_run_id or run_id
    if not (ws.run_dir(training_run_id) / "training").exists():
        print(f"training-run-id {training_run_id}: runs/{training_run_id}/training/ "
              "not found. Run `backtest train` first (or pass an existing one).")
        return
    if args.training_run_id and args.training_run_id != run_id:
        print(f"training source: runs/{training_run_id}/training/  "
              f"(outputs land in runs/{run_id}/)")

    until = _parse_iso_date(args.until_date) or _utcnow()
    from_date = _parse_iso_date(args.from_date) or default_from
    resuming = (ws.run_dir(run_id) / "portfolio_checkpoint.pkl").exists()

    print(f"run_id = {run_id}{'  (resuming)' if resuming else ''}")
    print(f"coins ({len(coins)}): {', '.join(coins)}")
    print(f"window: "
          f"{from_date.strftime('%Y-%m-%d') if from_date else '<earliest viable per coin>'}"
          f" → {until.strftime('%Y-%m-%d')}")
    print(f"starting: ${args.starting_usd:,.0f}  "
          f"params: lvl{args.lvl} a{args.alloc} p{args.pm}")
    print(f"report: backtest/runs/{run_id}/report.jsonl  (tail -f to monitor)")

    rpt.event(
        run_id, "run_started",
        subcommand="run" if default_from is None else "pilot",
        coins=coins,
        params={"lvl": args.lvl, "alloc": float(args.alloc),
                "pm": float(args.pm), "starting_usd": float(args.starting_usd),
                "from_date": str(from_date) if from_date else None,
                "until_date": str(until)},
    )
    t0 = time.monotonic()

    cfg = PortfolioRunConfig(
        coins=coins,
        starting_usd=float(args.starting_usd),
        until=until,
        from_date=from_date,
        snapshot_every_n=288,   # daily
        params=PortfolioParams(
            trade_start_level=int(args.lvl),
            start_allocation_pct=float(args.alloc),
            pm_start_pct=float(args.pm),
        ),
        training_run_id=(args.training_run_id or None),
    )

    try:
        if getattr(args, "profile", False):
            import cProfile, pstats, io as _io
            prof_path = ws.run_dir(run_id) / "engine.prof"
            ws.ensure_dir(ws.run_dir(run_id))
            pr = cProfile.Profile()
            pr.enable()
            try:
                res = run_portfolio(run_id, cfg)
            finally:
                pr.disable()
                pr.dump_stats(str(prof_path))
                buf = _io.StringIO()
                pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(40)
                (ws.run_dir(run_id) / "engine.prof.top40.txt").write_text(buf.getvalue())
                print(f"profile -> {prof_path}")
                print(f"         {ws.run_dir(run_id) / 'engine.prof.top40.txt'}")
        else:
            res = run_portfolio(run_id, cfg)
    except Exception as e:
        rpt.event(run_id, "run_failed", error=f"{type(e).__name__}: {e}")
        raise

    rpt.event(
        run_id, "run_engine_done",
        elapsed_s=time.monotonic() - t0,
        coins_active=res.coins_active,
        coins_skipped=res.coins_skipped,
        bars_processed=res.bars_processed,
        bars_resumed=res.bars_resumed,
        fills=len(res.fills),
        snapshots=len(res.series),
    )

    print("\naggregating ...")
    daily = pa.write_portfolio_daily(run_id)
    if daily is not None and not daily.empty:
        start_v = float(daily["total_account_value"].iloc[0])
        last_v = float(daily["total_account_value"].iloc[-1])
        ret = (last_v / start_v - 1.0) * 100.0 if start_v else 0.0
        resid = pa.attribution_residual(daily).abs().max()
        print(f"final ${last_v:,.2f}  return={ret:+.2f}%  "
              f"attribution_residual={resid:.2e}%")
        rpt.event(
            run_id, "run_completed",
            elapsed_s=time.monotonic() - t0,
            final_total_account_value=last_v,
            pct_return_compound=ret,
            attribution_residual=float(resid),
        )
    else:
        rpt.event(run_id, "run_completed",
                  elapsed_s=time.monotonic() - t0,
                  note="no daily produced (no snapshots)")


def cmd_run(args):
    """Full joint backtest — every coin from its earliest_viable_asof."""
    _cmd_portfolio(args, default_from=None)


def cmd_pilot(args):
    """Joint backtest defaulting to the last ~6 months for fast iteration."""
    _cmd_portfolio(args, default_from=_utcnow() - pd.Timedelta(days=180))


# ---------------------------------------------------------------------------
# Joint sweep over (lvl, alloc, pm)
# ---------------------------------------------------------------------------

# Each sweep task is one full joint backtest at fixed params. Tasks are
# independent (different sub-run dirs) but share the training tree of the
# parent run_id. Ray-parallel by default.

_DEFAULT_LVLS = [1, 2, 3]
_DEFAULT_ALLOCS = [0.5, 1.0, 2.0]
_DEFAULT_PMS = [2.0, 4.0, 6.0]


def _parse_csv_floats(s: Optional[str], default: list) -> list[float]:
    if not s:
        return [float(x) for x in default]
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def _parse_csv_ints(s: Optional[str], default: list) -> list[int]:
    if not s:
        return [int(x) for x in default]
    return [int(x.strip()) for x in s.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Concurrency policy (sweep)
# ---------------------------------------------------------------------------

# Per-worker memory budget. Calibrated against a 5y × 28-coin joint
# backtest after eliminating the trade-history scan (the biggest single
# memory hog in the engine — see backtest/trader.py):
#
#   Phase            Resident         What dominates
#   ----------------+----------------+--------------------------------------
#   load TF frames   ~110 MiB/coin    Arctic LMDB reads → numpy projections;
#                                     DataFrames briefly held then freed
#   build master idx ~0.3 GiB peak    np.concatenate + np.unique on i8 ns
#   post-build       ~110 MiB/coin    Walk-steady-state, dominated by per-
#                                     coin grid5_* + tf_ts_ns/opens arrays
#   walk             same             No accumulation observed: fills/series
#                                     are appended dicts, tiny vs the data
#
# Observed walk-steady-state on the wire after trade-history fix:
# 5-coin pilot ~557 MiB, scales to ~3 GiB at 28 coins. No transient
# spike. 4 GiB budget covers steady-state + comfortable headroom.
_PER_WORKER_GB = 4.0

# Always leave this much free for the driver, Ray's own bookkeeping, and the
# host OS. Computed on top of the per-worker reserve.
_DRIVER_RESERVE_GB = 4.0

# Ray's Plasma object store. Defaults to ~30 % of system RAM (≈10 GiB on a
# 32 GiB box) which is overkill — our task results are tiny dicts. Cap at
# 2 GiB so it doesn't compete with worker resident sets.
_RAY_OBJECT_STORE_GB = 2.0


def _available_memory_gb() -> float:
    """Best-effort MemAvailable in GiB. Reads /proc/meminfo on Linux/WSL2;
    falls back to a conservative 8 GiB if anything goes wrong (e.g. macOS,
    container without /proc, permission denied).
    """
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = float(line.split()[1])
                    return kb / (1024 * 1024)
    except Exception:
        pass
    return 8.0


def _auto_max_parallel() -> int:
    """Default `--max-parallel` for a sweep on this host.

    Bounded by both CPU and memory:
      cpu_cap  = min(8, os.cpu_count())  — Ray's per-worker accounting
                  scales sub-linearly past ~8 on this engine.
      mem_cap  = floor((MemAvailable − driver_reserve) / per_worker_gb)
    The minimum of those two, never less than 1, never more than 16.
    """
    import os
    cpu_cap = min(8, os.cpu_count() or 4)
    avail_gb = _available_memory_gb()
    headroom = max(0.0, avail_gb - _DRIVER_RESERVE_GB)
    mem_cap = max(1, int(headroom // _PER_WORKER_GB))
    return max(1, min(16, cpu_cap, mem_cap))


def _norm_nan(v):
    """JSONL-safe normaliser: NaN/inf become None.

    `json.dumps(float('nan'))` emits the literal `NaN`, which most JSON
    parsers reject. The sweep_progress events go to `report.jsonl` that
    downstream tools tail; keep it strict.
    """
    import math
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _emit_sweep_progress(parent_run_id: str, n_done: int, n_total: int,
                        res: dict) -> None:
    """Log one sweep_progress event + a one-line stdout summary.

    Called once per sub-run completion in both Ray and serial modes. The
    JSONL event lets `tail -f report.jsonl` show a live `12/27 done` feed
    even when worker stdout is going elsewhere.
    """
    label = f"lvl{int(res['lvl'])}_a{res['alloc']}_p{res['pm']}"
    pct = _norm_nan(res.get("pct_return"))
    final_v = _norm_nan(res.get("final_value"))
    rpt.event(
        parent_run_id, "sweep_progress",
        n_done=int(n_done), n_total=int(n_total),
        sub_run_id=res.get("sub_run_id"),
        lvl=int(res["lvl"]), alloc=float(res["alloc"]), pm=float(res["pm"]),
        pct_return=pct,
        final_value=final_v,
        n_fills=int(res.get("n_fills") or 0),
        error=res.get("error"),
    )
    pct_s = f"{pct:+.2f}%" if pct is not None else "n/a"
    err_s = f"  ERROR={res['error']}" if res.get("error") else ""
    print(f"[sweep] {n_done}/{n_total} done · {label} "
          f"return={pct_s}  fills={int(res.get('n_fills') or 0)}{err_s}",
          flush=True)


def _sweep_already_done(parent: str, lvl: int, alloc: float, pm: float) -> bool:
    """True iff this grid point already has both completion markers on disk.

    `portfolio_daily.parquet` is the final aggregation artefact (written
    after `series.parquet`), so its presence means the sub-run completed
    cleanly. `series.parquet` alone wouldn't be sufficient — a sub-run
    could have crashed between writing series and writing daily.
    """
    from . import workspace as ws
    sub_id = _sweep_sub_run_id(parent, lvl, alloc, pm)
    sub_dir = ws.run_dir(sub_id)
    return (
        (sub_dir / "series.parquet").exists()
        and (sub_dir / "portfolio_daily.parquet").exists()
    )


def _sweep_load_done_result(parent: str, lvl: int, alloc: float, pm: float,
                            starting_usd: float) -> dict:
    """Reconstruct the row that the worker would have returned, by reading
    `portfolio_daily.parquet` directly. Saves re-running an already-done
    sub-run while still producing a complete `sweep_results.parquet`."""
    sub_id = _sweep_sub_run_id(parent, lvl, alloc, pm)
    sub_dir = ws.run_dir(sub_id)
    out = {
        "lvl": int(lvl), "alloc": float(alloc), "pm": float(pm),
        "sub_run_id": sub_id, "starting_value": float(starting_usd),
        "final_value": float("nan"), "pct_return": float("nan"),
        "n_fills": 0, "n_snapshots": 0,
        "coins_active": 0, "coins_skipped": 0,
        "error": None, "from_cache": True,
    }
    try:
        daily = pd.read_parquet(sub_dir / "portfolio_daily.parquet")
        if not daily.empty:
            start_v = float(daily["total_account_value"].iloc[0])
            last_v = float(daily["total_account_value"].iloc[-1])
            out["starting_value"] = start_v
            out["final_value"] = last_v
            out["pct_return"] = (
                ((last_v / start_v) - 1.0) * 100.0 if start_v else 0.0
            )
            out["n_snapshots"] = int(len(daily))
        series_path = sub_dir / "series.parquet"
        if series_path.exists():
            n_fills = pd.read_parquet(
                series_path, columns=["ts"],
            ).shape[0]  # rough; real count is in fills.parquet if present
            fills_path = sub_dir / "fills.parquet"
            if fills_path.exists():
                out["n_fills"] = int(
                    pd.read_parquet(fills_path, columns=["ts"]).shape[0]
                )
    except Exception as e:
        out["error"] = f"cache-read failed: {type(e).__name__}: {e}"
    return out


def _sweep_sub_run_id(parent: str, lvl: int, alloc: float, pm: float) -> str:
    # Nested under the parent so the sweep is one tidy subtree on disk:
    # runs/<parent>/sweep/l<L>_a<A>_p<P>/. ws.run_dir resolves the
    # forward slash to a real path component.
    return f"{parent}/sweep/l{lvl}_a{alloc}_p{pm}"


def _sweep_worker(
    parent_run_id: str,
    coins: list[str],
    starting_usd: float,
    until_iso: Optional[str],
    from_iso: Optional[str],
    lvl: int,
    alloc: float,
    pm: float,
) -> dict:
    """One sweep task: full joint backtest at fixed params + aggregation.

    Args are plain Python types (str / float / list) so Ray can pickle
    them cleanly. Returns a metrics dict that becomes one row of
    sweep_results.parquet.
    """
    from . import portfolio_aggregate as pa_inner
    from .portfolio_engine import (
        PortfolioParams as PP,
        PortfolioRunConfig as PRC,
        run_portfolio as rp,
    )

    until = pd.Timestamp(until_iso) if until_iso else None
    if until is not None and until.tz is None:
        until = until.tz_localize("UTC")
    from_date = pd.Timestamp(from_iso) if from_iso else None
    if from_date is not None and from_date.tz is None:
        from_date = from_date.tz_localize("UTC")

    sub_run_id = _sweep_sub_run_id(parent_run_id, lvl, alloc, pm)

    cfg = PRC(
        coins=list(coins),
        starting_usd=float(starting_usd),
        until=until,
        from_date=from_date,
        snapshot_every_n=288,
        params=PP(
            trade_start_level=int(lvl),
            start_allocation_pct=float(alloc),
            pm_start_pct=float(pm),
        ),
        training_run_id=parent_run_id,
    )

    out = {
        "lvl": int(lvl),
        "alloc": float(alloc),
        "pm": float(pm),
        "sub_run_id": sub_run_id,
        "starting_value": float(starting_usd),
        "final_value": float("nan"),
        "pct_return": float("nan"),
        "n_fills": 0,
        "n_snapshots": 0,
        "coins_active": 0,
        "coins_skipped": 0,
        "error": None,
    }
    try:
        res = rp(sub_run_id, cfg)
        out["n_fills"] = int(len(res.fills))
        out["n_snapshots"] = int(len(res.series))
        out["coins_active"] = int(len(res.coins_active))
        out["coins_skipped"] = int(len(res.coins_skipped))
        if res.error:
            out["error"] = res.error
            return out
        daily = pa_inner.write_portfolio_daily(sub_run_id)
        if daily is not None and not daily.empty:
            start_v = float(daily["total_account_value"].iloc[0])
            last_v = float(daily["total_account_value"].iloc[-1])
            out["starting_value"] = start_v
            out["final_value"] = last_v
            out["pct_return"] = ((last_v / start_v) - 1.0) * 100.0 if start_v else 0.0
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def cmd_sweep(args):
    """Joint sweep over (lvl, alloc, pm). Requires --run-id of a prior
    `train` run; each param point becomes a sub-run sharing that training
    tree. Ray-parallel by default."""
    parent_run_id = args.run_id
    if not parent_run_id:
        print("sweep: --run-id is required and must point at a `train` run "
              "whose training_data.json artifacts will be shared across all "
              "sweep sub-runs. Run `backtest train` first.")
        return

    parent_dir = ws.run_dir(parent_run_id)
    if not (parent_dir / "training").exists():
        print(f"sweep: runs/{parent_run_id}/training/ not found — "
              "is this a real train run_id?")
        return

    coins = _resolve_coins(args.coin)
    if not coins:
        print("sweep: no coins")
        return

    lvls = _parse_csv_ints(args.lvls, _DEFAULT_LVLS)
    allocs = _parse_csv_floats(args.allocs, _DEFAULT_ALLOCS)
    pms = _parse_csv_floats(args.pms, _DEFAULT_PMS)
    grid: list[tuple[int, float, float]] = [
        (lvl, alloc, pm) for lvl in lvls for alloc in allocs for pm in pms
    ]

    until = _parse_iso_date(args.until_date) or _utcnow()
    from_date = _parse_iso_date(args.from_date)
    until_iso = until.isoformat() if until is not None else None
    from_iso = from_date.isoformat() if from_date is not None else None

    print(f"sweep parent run_id = {parent_run_id}")
    print(f"coins ({len(coins)}): {', '.join(coins)}")
    print(f"grid: {len(lvls)} lvls × {len(allocs)} allocs × {len(pms)} pms = "
          f"{len(grid)} points  ({'parallel' if not args.serial else 'serial'})")
    print(f"window: "
          f"{from_date.strftime('%Y-%m-%d') if from_date else '<earliest viable>'}"
          f" → {until.strftime('%Y-%m-%d')}")
    print(f"starting: ${args.starting_usd:,.0f}")
    print(f"report: backtest/runs/{parent_run_id}/report.jsonl")

    rpt.event(
        parent_run_id, "run_started",
        subcommand="sweep",
        coins=coins,
        params={"grid_size": len(grid),
                "lvls": lvls, "allocs": allocs, "pms": pms,
                "starting_usd": float(args.starting_usd),
                "from_date": str(from_date) if from_date else None,
                "until_date": str(until),
                "serial": bool(args.serial)},
    )
    t0 = time.monotonic()

    use_ray = not args.serial
    if use_ray:
        try:
            import ray  # type: ignore
        except ImportError:
            print("[sweep] Ray not installed — falling back to serial")
            use_ray = False

    if use_ray:
        import ray  # type: ignore
        # Concurrency cap. Default protects WSL2: each sub-run loads a
        # multi-GiB price + training-data footprint, and 16 logical CPUs
        # running concurrently can blow past the WSL memory ceiling.
        max_parallel = (
            int(args.max_parallel) if args.max_parallel is not None
            else _auto_max_parallel()
        )
        max_parallel = max(1, min(max_parallel, len(grid)))
        avail_gb = _available_memory_gb()
        _stagger_s = float(args.stagger) if args.stagger is not None else 90.0
        print(f"[sweep] concurrency cap: {max_parallel} workers  "
              f"(MemAvailable {avail_gb:.1f} GiB, "
              f"per-worker reserve {_PER_WORKER_GB:.0f} GiB, "
              f"Ray object store {_RAY_OBJECT_STORE_GB:.0f} GiB, "
              f"max_calls=1, max_retries=0, "
              f"stagger {_stagger_s:.0f}s)")
        rpt.event(
            parent_run_id, "sweep_concurrency",
            max_parallel=max_parallel,
            available_gb=round(avail_gb, 1),
            per_worker_gb=_PER_WORKER_GB,
            grid_size=len(grid),
        )

        if not ray.is_initialized():
            # num_cpus caps the worker pool. log_to_driver=True streams
            # per-worker engine heartbeats to this terminal — noisy with
            # many workers but invaluable for spotting a stuck point.
            # Set RAY_LOG_TO_DRIVER=0 in env to override if it overwhelms.
            #
            # object_store_memory: Ray defaults to ~30 % of RAM for the
            # Plasma store. Our task results are tiny dicts — capping at
            # _RAY_OBJECT_STORE_GB stops Plasma from competing with worker
            # resident sets for the same memory page.
            ray.init(
                ignore_reinit_error=True,
                log_to_driver=True,
                num_cpus=max_parallel,
                object_store_memory=int(_RAY_OBJECT_STORE_GB * 1024**3),
            )
        # max_calls=1     forces a fresh Python process per sub-run.
        #                 Each _sweep_worker imports pandas / arctic /
        #                 pt_trader and accumulates frame caches, pyarrow
        #                 buffers, and a fills/series list of its own —
        #                 none of which fully release between tasks if
        #                 the worker is reused. ~1 s fork overhead per
        #                 task is invisible against 5-30 min of real work.
        # max_retries=0   the Ray default of 3 retries is dangerous here:
        #                 if a sub-run is killed by the OOM monitor at
        #                 RAM saturation, Ray re-launches the same task
        #                 with the same memory profile, which dies the
        #                 same way. Zero retries → fail fast, report the
        #                 error once, free the slot for the next point.
        remote = ray.remote(max_calls=1, max_retries=0)(_sweep_worker)

        # Pre-flight: split the grid into "already done" (load result
        # from disk) and "needs running" (submit to Ray). A sub-run is
        # considered done when both series.parquet and
        # portfolio_daily.parquet exist in its dir.
        cached_results: list[dict] = []
        live_grid: list[tuple[int, float, float]] = []
        for (lvl, alloc, pm) in grid:
            if _sweep_already_done(parent_run_id, lvl, alloc, pm):
                cached_results.append(_sweep_load_done_result(
                    parent_run_id, lvl, alloc, pm, float(args.starting_usd),
                ))
            else:
                live_grid.append((lvl, alloc, pm))
        if cached_results:
            print(f"[sweep] skipping {len(cached_results)} already-done "
                  f"sub-runs; running {len(live_grid)} fresh")
            rpt.event(
                parent_run_id, "sweep_resume",
                n_cached=len(cached_results), n_to_run=len(live_grid),
                n_total=len(grid),
            )

        # Sliding-window submission. We never have more than `max_parallel`
        # futures in flight; new ones are submitted only as old ones drain.
        # Belt-and-suspenders with num_cpus above: bounds Ray's scheduler
        # queue too, keeping the driver's reference-list small.
        #
        # future_to_params: tracks which (lvl, alloc, pm) is behind each
        # ObjectRef so that when Ray's OOM monitor kills a worker (which
        # picks the most recently scheduled task as the victim — NOT
        # necessarily the actual memory hog) we still know which combo
        # the killed worker was running. Without this, all OOM-killed
        # rows in sweep_results.parquet would have sentinel (-1,-1,-1)
        # params, blocking targeted re-runs.
        future_to_params: dict = {}
        pending = list(live_grid)
        in_flight: list = []
        results: list = list(cached_results)
        for res in cached_results:
            _emit_sweep_progress(parent_run_id, len(results), len(grid), res)
        # Stagger between worker submissions (seconds). Each new worker
        # spawns a fresh Python process whose first ~60-120 s of work is
        # memory-heavy: arctic decompresses 196 kucoin{N} frames into
        # pandas (transient ~2× RSS bump before tf_frames.clear() runs),
        # then the trader processes bar 0 with all 28 coins' epoch-zero
        # training-data parses, then the first manage_trades call sets
        # up internal state for every coin. Letting one worker get past
        # that entire phase before the next starts prevents concurrent
        # peaks colliding under Ray's 95 % OOM monitor.
        #
        # 90 s is calibrated empirically — Daisy observed that 15 s
        # wasn't enough to avoid OOM kills on a 32 GiB WSL2 box with 2
        # concurrent workers. Total overhead: (max_parallel-1) × stagger,
        # e.g. 90 s on a 2-worker sweep — invisible against minutes-to-
        # hours of work per sub-run. Override via --stagger N.
        _STAGGER_S = float(args.stagger) if args.stagger is not None else 90.0
        _last_submit_t = 0.0
        while pending or in_flight:
            while pending and len(in_flight) < max_parallel:
                # Throttle submissions so workers don't all hit the
                # data-load peak at the same instant. The first task
                # submits immediately (gap = inf since _last_submit_t=0).
                _gap = time.monotonic() - _last_submit_t
                if _gap < _STAGGER_S and len(in_flight) > 0:
                    break  # fall through to ray.wait until stagger elapses
                lvl, alloc, pm = pending.pop(0)
                fut = remote.remote(
                    parent_run_id, coins, float(args.starting_usd),
                    until_iso, from_iso, lvl, alloc, pm,
                )
                future_to_params[fut] = (lvl, alloc, pm)
                in_flight.append(fut)
                _last_submit_t = time.monotonic()
                print(f"[sweep] launched lvl{lvl}_a{alloc}_p{pm}  "
                      f"({len(in_flight)}/{max_parallel} workers active, "
                      f"{len(pending)} pending)", flush=True)
            if in_flight:
                # ray.wait with a short timeout so we can re-check the
                # stagger condition for pending submissions. Without
                # the timeout, ray.wait would block until a worker
                # completes — possibly many minutes — even if the
                # stagger window has elapsed.
                _wait_to = max(1.0, _STAGGER_S - (time.monotonic() - _last_submit_t))
                done_refs_x, _maybe_remaining = ray.wait(
                    in_flight, num_returns=1, timeout=_wait_to,
                )
                if not done_refs_x:
                    continue   # stagger elapsed, loop to submit next
                done_refs = done_refs_x
                in_flight = _maybe_remaining
                done_fut = done_refs[0]
                done_lvl, done_alloc, done_pm = future_to_params.pop(
                    done_fut, (-1, -1.0, -1.0),
                )
                try:
                    res = ray.get(done_fut)
                except Exception as e:
                    # ray.get re-raises worker exceptions (incl. OOM kills).
                    # Surface them as a result row so the rollup is complete,
                    # tagged with the actual combo (not -1 sentinels) so
                    # re-launching with the same --run-id targets the right
                    # sub-run dir.
                    sub_id = (
                        _sweep_sub_run_id(
                            parent_run_id, done_lvl, done_alloc, done_pm,
                        ) if done_lvl != -1 else None
                    )
                    res = {
                        "lvl": int(done_lvl),
                        "alloc": float(done_alloc),
                        "pm": float(done_pm),
                        "sub_run_id": sub_id,
                        "starting_value": float(args.starting_usd),
                        "final_value": float("nan"),
                        "pct_return": float("nan"),
                        "n_fills": 0, "n_snapshots": 0,
                        "coins_active": 0, "coins_skipped": 0,
                        "error": f"ray.get failed: {type(e).__name__}: {e}",
                    }
                results.append(res)
                _emit_sweep_progress(parent_run_id, len(results), len(grid), res)
    else:
        # Serial path mirrors the same skip-completed logic for resumability.
        results = []
        for (lvl, alloc, pm) in grid:
            if _sweep_already_done(parent_run_id, lvl, alloc, pm):
                res = _sweep_load_done_result(
                    parent_run_id, lvl, alloc, pm, float(args.starting_usd),
                )
            else:
                res = _sweep_worker(
                    parent_run_id, coins, float(args.starting_usd),
                    until_iso, from_iso, lvl, alloc, pm,
                )
            results.append(res)
            _emit_sweep_progress(parent_run_id, len(results), len(grid), res)

    # Headline rollup (one row per param point)
    res_df = pd.DataFrame(results)
    rollup_path = parent_dir / "sweep_results.parquet"
    res_df.to_parquet(rollup_path)

    # Long-format daily timeseries across every param point
    sweep_daily = pa.write_sweep_daily(parent_run_id)
    elapsed = time.monotonic() - t0

    n_ok = int(res_df["error"].isna().sum())
    n_err = int(len(res_df) - n_ok)
    print(f"\ncompleted {len(res_df)} sweep tasks in {elapsed:.1f}s  "
          f"({n_ok} ok, {n_err} errors)")
    if n_ok:
        ok = res_df[res_df["error"].isna()].sort_values("pct_return", ascending=False)
        print(f"\ntop 5 by pct_return:")
        for _, row in ok.head(5).iterrows():
            print(f"  lvl{int(row['lvl'])} a{row['alloc']} p{row['pm']:<4}  "
                  f"return={row['pct_return']:+.2f}%  "
                  f"final=${row['final_value']:,.0f}  "
                  f"fills={int(row['n_fills'])}")
    if n_err:
        print(f"\nerrors ({n_err}):")
        for _, row in res_df[res_df["error"].notna()].head(5).iterrows():
            print(f"  lvl{int(row['lvl'])} a{row['alloc']} p{row['pm']}: "
                  f"{row['error']}")

    rpt.event(parent_run_id, "run_completed",
              elapsed_s=elapsed, n_ok=n_ok, n_err=n_err,
              rollup_path=str(rollup_path))
    print(f"\nsweep_results.parquet  -> {rollup_path}  ({len(res_df)} rows)")
    if sweep_daily is not None:
        print(f"sweep_daily.parquet    -> {parent_dir / 'sweep_daily.parquet'}  "
              f"({len(sweep_daily):,} rows)")
    else:
        print("sweep_daily.parquet    -> (skipped — no sub-run produced a daily series)")


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def cmd_train(args):
    """Produce training_data.json for every (coin × epoch). Ray-parallel
    across the full grid by default. Subsequent `run` invocations with
    the same --run-id reuse this output via skip-if-done."""
    coins = _resolve_coins(args.coin)
    if not coins:
        print("no coins to train")
        return

    src = ArcticPriceSource()
    run_id = args.run_id or ws.new_run_id(prefix="train")
    print(f"run_id = {run_id}{'  (resuming)' if args.run_id else ''}")
    print(f"coins ({len(coins)}): {', '.join(coins)}")

    t0 = time.time()
    by_coin = train_grid(
        run_id=run_id,
        coins=coins,
        until=_utcnow(),
        parallel=not args.serial,
        epochs_per_coin=args.epochs,
        price_source=src,
    )
    elapsed = time.time() - t0

    print(f"\nFinished in {elapsed:.1f}s "
          f"({'serial' if args.serial else 'Ray-parallel'})")
    failed_rows: list[tuple[str, str, str]] = []
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


# ---------------------------------------------------------------------------
# Aggregate (re-derive)
# ---------------------------------------------------------------------------

def cmd_aggregate(args):
    """Re-derive portfolio_daily.parquet from an existing joint run."""
    run_id = args.run_id_pos or args.run_id_kwarg
    if not run_id:
        print("aggregate: run_id required "
              "(positional 'run_id' or --run-id <id>)")
        return

    print(f"aggregating run_id={run_id}")
    daily = pa.write_portfolio_daily(run_id)
    if daily is None:
        print(f"no series.parquet at runs/{run_id}/ — "
              "nothing to aggregate (was this run produced by the "
              "joint engine?)")
        return
    if daily.empty:
        print("series.parquet present but empty")
        return
    resid = pa.attribution_residual(daily).abs().max()
    print(f"portfolio_daily rows: {len(daily)}  "
          f"max attribution residual: {resid:.2e}%")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _add_replay_args(sp: argparse.ArgumentParser) -> None:
    """Args shared by `run` and `pilot`."""
    sp.add_argument("--coin", default=None,
                    help="Coin symbol, comma-separated list, "
                         "or omit for all pt_config.json coins")
    sp.add_argument("--lvl", type=int, default=2,
                    help="trade_start_level (default 2)")
    sp.add_argument("--alloc", type=float, default=1.0,
                    help="start_allocation_pct (default 1.0)")
    sp.add_argument("--pm", type=float, default=4.0,
                    help="pm_start_pct (default 4.0)")
    sp.add_argument("--starting-usd", type=float, default=10000.0,
                    help="Joint wallet starting cash (default 10000)")
    sp.add_argument("--from-date", default=None,
                    help="Earliest snapshot, ISO YYYY-MM-DD "
                         "(default: pilot=~6mo ago, run=earliest viable)")
    sp.add_argument("--until-date", default=None,
                    help="Latest snapshot, ISO YYYY-MM-DD (default: now)")
    sp.add_argument("--run-id", required=True,
                    help="Train run_id whose training tree to reuse, and "
                         "where replay outputs land. Re-issuing the same "
                         "id on a later run resumes from the checkpoint.")
    sp.add_argument("--training-run-id", default=None,
                    help="Optional override: read training_data.json from "
                         "this run_id's training/ tree instead of --run-id's. "
                         "Lets a side experiment (profile, sweep sub-run) "
                         "write its own outputs without touching another "
                         "in-flight run's checkpoint.")
    sp.add_argument("--profile", action="store_true",
                    help="Wrap run_portfolio() in cProfile; dump "
                         "engine.prof + engine.prof.top40.txt into the run dir.")


def main():
    p = argparse.ArgumentParser(prog="backtest")
    sub = p.add_subparsers(dest="cmd", required=True)

    pilot = sub.add_parser(
        "pilot",
        help="Joint backtest, last ~6 months by default — minutes of compute",
    )
    _add_replay_args(pilot)
    pilot.set_defaults(func=cmd_pilot)

    run = sub.add_parser(
        "run",
        help="Joint backtest, earliest viable per coin → now",
    )
    _add_replay_args(run)
    run.set_defaults(func=cmd_run)

    sweep = sub.add_parser(
        "sweep",
        help="Joint sweep over (lvl, alloc, pm) — Ray task per param point",
    )
    sweep.add_argument("--coin", default=None,
                       help="Coin symbol, comma-separated list, "
                            "or omit for all pt_config.json coins")
    sweep.add_argument("--run-id", required=True,
                       help="Parent run_id with training data "
                            "(produced by `backtest train`)")
    sweep.add_argument("--lvls", default=None,
                       help=f"Comma-separated trade_start_level values "
                            f"(default: {','.join(map(str, _DEFAULT_LVLS))})")
    sweep.add_argument("--allocs", default=None,
                       help=f"Comma-separated start_allocation_pct values "
                            f"(default: {','.join(map(str, _DEFAULT_ALLOCS))})")
    sweep.add_argument("--pms", default=None,
                       help=f"Comma-separated pm_start_pct values "
                            f"(default: {','.join(map(str, _DEFAULT_PMS))})")
    sweep.add_argument("--starting-usd", type=float, default=10000.0,
                       help="Joint wallet starting cash per sub-run (default 10000)")
    sweep.add_argument("--from-date", default=None,
                       help="Earliest snapshot, ISO YYYY-MM-DD "
                            "(default: earliest viable)")
    sweep.add_argument("--until-date", default=None,
                       help="Latest snapshot, ISO YYYY-MM-DD (default: now)")
    sweep.add_argument("--serial", action="store_true",
                       help="Disable Ray; run param points sequentially")
    sweep.add_argument(
        "--max-parallel", type=int, default=None,
        help="Maximum concurrent sub-runs in Ray mode. Default is "
             "min(8, cpus, (MemAvailable - 4 GiB) // 8 GiB) — bounded by "
             "both CPU and memory so a big sweep can't OOM WSL2. Pass "
             "an explicit integer to override (1 = effectively serial).",
    )
    sweep.add_argument(
        "--stagger", type=float, default=None,
        help="Seconds between worker submissions (default 90). Each "
             "fresh worker spikes RAM during the 60-120 s data-load + "
             "bar-0 phase before settling to walk steady-state; the "
             "stagger lets one worker finish that phase before the next "
             "starts, so concurrent peaks don't collide. Drop to 0 to "
             "submit all workers at once; raise if RAM still spikes.",
    )
    sweep.set_defaults(func=cmd_sweep)

    train = sub.add_parser(
        "train",
        help="Produce all training_data.json artifacts "
             "(Ray-parallel across coin × epoch)",
    )
    train.add_argument("--coin", default=None,
                       help="Coin symbol, comma-separated list, "
                            "or omit for all pt_config.json coins")
    train.add_argument("--epochs", type=int, default=None,
                       help="Cap epochs per coin (default: all viable)")
    train.add_argument("--run-id", default=None,
                       help="Existing run_id to resume (default: new timestamped)")
    train.add_argument("--serial", action="store_true",
                       help="Disable Ray; train coin × epoch sequentially")
    train.set_defaults(func=cmd_train)

    aggr = sub.add_parser(
        "aggregate",
        help="Re-derive portfolio_daily.parquet from an existing run",
    )
    aggr.add_argument("run_id_pos", nargs="?", default=None, metavar="run_id",
                      help="Run ID to aggregate (positional, or use --run-id)")
    aggr.add_argument("--run-id", dest="run_id_kwarg", default=None,
                      help="Run ID to aggregate (alternative to positional)")
    aggr.set_defaults(func=cmd_aggregate)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
