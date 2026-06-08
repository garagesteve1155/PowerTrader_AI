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
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, log_to_driver=False)
        remote = ray.remote(_sweep_worker)
        futures = [
            remote.remote(parent_run_id, coins, float(args.starting_usd),
                          until_iso, from_iso, lvl, alloc, pm)
            for (lvl, alloc, pm) in grid
        ]
        results = ray.get(futures)
    else:
        results = [
            _sweep_worker(parent_run_id, coins, float(args.starting_usd),
                          until_iso, from_iso, lvl, alloc, pm)
            for (lvl, alloc, pm) in grid
        ]

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
