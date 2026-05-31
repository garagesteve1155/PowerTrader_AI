"""Marimo notebook for backtest research.

Run with:  marimo edit backtest/research.py

Cells:
  - Pick a run id
  - Load portfolio_hourly + daily
  - Equity curve
  - Daily returns histogram + summary stats
  - For sweep runs: 2D heatmaps over the param grid
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell
def _imports():
    import glob
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import numpy as np
    import pandas as pd
    return Path, alt, glob, mo, np, os, pd


@app.cell
def _run_picker(glob, mo, os):
    runs = sorted(glob.glob("backtest/runs/*"))
    run_ids = [os.path.basename(p) for p in runs if os.path.isdir(p)]
    if run_ids:
        run_picker = mo.ui.dropdown(options=run_ids, value=run_ids[-1], label="Run ID")
        run_picker_view = run_picker
    else:
        run_picker = None
        run_picker_view = mo.md(
            "**No backtest runs found.** Run "
            "`python3 -m backtest.cli pilot --coin ETH` first."
        )
    run_picker_view
    return (run_picker,)


@app.cell
def _load(pd, run_picker):
    if run_picker is None:
        portfolio_h = pd.DataFrame()
        daily = pd.DataFrame()
        run_id = None
    else:
        run_id = run_picker.value
        _agg_dir = f"backtest/runs/{run_id}/agg"
        try:
            portfolio_h = pd.read_parquet(f"{_agg_dir}/portfolio_hourly.parquet")
        except (FileNotFoundError, OSError):
            portfolio_h = pd.DataFrame()
        try:
            daily = pd.read_parquet(f"{_agg_dir}/portfolio_daily.parquet")
        except (FileNotFoundError, OSError):
            daily = pd.DataFrame()
    return daily, portfolio_h, run_id


@app.cell
def _equity_curve(alt, mo, portfolio_h):
    if portfolio_h.empty:
        equity_chart = mo.md(
            "*No `portfolio_hourly.parquet` yet — run "
            "`python3 -m backtest.cli aggregate <run_id> --coins ETH` "
            "to build it.*"
        )
    else:
        _df = portfolio_h.reset_index().rename(columns={"index": "ts"})
        if "ts" not in _df.columns:
            _df = _df.rename(columns={_df.columns[0]: "ts"})
        equity_chart = (
            alt.Chart(_df)
            .mark_line()
            .encode(
                x=alt.X("ts:T", title="Date"),
                y=alt.Y("total_account_value:Q", title="Portfolio Value ($)"),
            )
            .properties(width=700, height=240, title="Equity curve")
        )
    equity_chart
    return (equity_chart,)


@app.cell
def _daily_summary(daily, mo, np):
    if daily.empty or "daily_pct_return" not in daily.columns:
        daily_stats = mo.md("*No daily series.*")
    else:
        r = daily["daily_pct_return"].dropna()
        ann = r.mean() * 365.0
        ann_vol = r.std() * np.sqrt(365.0)
        sharpe = ann / ann_vol if ann_vol > 0 else float("nan")
        n_days = len(r)
        daily_stats = mo.md(
            f"**Daily stats over {n_days} days**\n\n"
            f"- Annualised return: **{ann:.2f}%**\n"
            f"- Annualised vol: **{ann_vol:.2f}%**\n"
            f"- Sharpe (rf=0): **{sharpe:.2f}**\n"
            f"- Best day: **{r.max():.2f}%**\n"
            f"- Worst day: **{r.min():.2f}%**\n"
        )
    daily_stats
    return (daily_stats,)


@app.cell
def _daily_hist(alt, daily, mo):
    if daily.empty or "daily_pct_return" not in daily.columns:
        hist_chart = mo.md("")
    else:
        _df = daily.reset_index().rename(columns={"index": "ts"})
        if "ts" not in _df.columns:
            _df = _df.rename(columns={_df.columns[0]: "ts"})
        hist_chart = (
            alt.Chart(_df)
            .mark_bar()
            .encode(
                x=alt.X(
                    "daily_pct_return:Q",
                    bin=alt.Bin(maxbins=40),
                    title="Daily % return",
                ),
                y=alt.Y("count()", title="Days"),
            )
            .properties(
                width=600,
                height=200,
                title="Distribution of daily returns",
            )
        )
    hist_chart
    return (hist_chart,)


@app.cell
def _sweep_panel(glob, os, pd, run_id):
    """If the loaded run is a sweep, gather sub-run final returns."""
    sibling_globs = (
        sorted(glob.glob(f"backtest/runs/{run_id}__*"))
        if run_id is not None else []
    )
    rows = []
    for sib in sibling_globs:
        sib_id = os.path.basename(sib)
        try:
            tail = sib_id.split("__")[-1]
            parts = tail.split("_")
            lvl = int(parts[0][1:])
            alloc = int(parts[1][1:])
            pm = int(parts[2][1:])
        except (ValueError, IndexError):
            continue
        _sweep_agg_dir = f"{sib}/agg"
        try:
            _ph = pd.read_parquet(f"{_sweep_agg_dir}/portfolio_hourly.parquet")
            _total_ret = float(_ph["pct_return"].iloc[-1])
        except (FileNotFoundError, OSError, KeyError, IndexError):
            continue
        rows.append({"lvl": lvl, "alloc": alloc, "pm": pm, "return_pct": _total_ret})

    sweep_df = pd.DataFrame(rows)
    return (sweep_df,)


@app.cell
def _sweep_heatmap(alt, mo, sweep_df):
    if sweep_df.empty:
        sweep_chart = mo.md("*No sweep sub-runs detected for this run.*")
    else:
        agg_df = (
            sweep_df.groupby(["lvl", "alloc"])["return_pct"]
            .mean()
            .reset_index()
        )
        sweep_chart = (
            alt.Chart(agg_df)
            .mark_rect()
            .encode(
                x=alt.X("lvl:O", title="trade_start_level"),
                y=alt.Y("alloc:O", title="start_allocation_pct"),
                color=alt.Color("return_pct:Q", title="Mean % return"),
                tooltip=["lvl", "alloc", "return_pct"],
            )
            .properties(
                width=500,
                height=300,
                title="Mean total return by (lvl, alloc), avg over pm",
            )
        )
    sweep_chart
    return (sweep_chart,)


if __name__ == "__main__":
    app.run()
