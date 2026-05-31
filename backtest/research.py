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

__generated_with = "0.7.0"
app = marimo.App(width="medium")


@app.cell
def _imports():
    import glob
    import json
    import os
    from pathlib import Path
    import marimo as mo
    import numpy as np
    import pandas as pd
    import altair as alt
    return alt, glob, json, mo, np, os, pd, Path


@app.cell
def _run_picker(glob, mo, os):
    runs = sorted(glob.glob("backtest/runs/*"))
    run_ids = [os.path.basename(p) for p in runs if os.path.isdir(p)]
    if not run_ids:
        mo.md("**No backtest runs found. Run `python3 -m backtest.cli pilot --coin ETH` first.**")
        run_picker = None
    else:
        run_picker = mo.ui.dropdown(options=run_ids, value=run_ids[-1], label="Run ID")
    return run_picker, run_ids


@app.cell
def _show_picker(mo, run_picker):
    mo.md(f"**Available runs:** {len(run_picker.options) if run_picker else 0}") if run_picker else None
    return


@app.cell
def _load(pd, run_picker):
    if run_picker is None:
        portfolio_h = pd.DataFrame()
        daily = pd.DataFrame()
        coin_files = []
    else:
        run_id = run_picker.value
        agg_dir = f"backtest/runs/{run_id}/agg"
        try:
            portfolio_h = pd.read_parquet(f"{agg_dir}/portfolio_hourly.parquet")
        except FileNotFoundError:
            portfolio_h = pd.DataFrame()
        try:
            daily = pd.read_parquet(f"{agg_dir}/portfolio_daily.parquet")
        except FileNotFoundError:
            daily = pd.DataFrame()
        import glob
        coin_files = sorted(glob.glob(f"{agg_dir}/*_hourly.parquet"))
    return coin_files, daily, portfolio_h, run_picker


@app.cell
def _equity_curve(alt, daily, mo, pd, portfolio_h):
    if portfolio_h.empty:
        mo.md("*No portfolio_hourly.parquet yet — run `aggregate` first.*")
    else:
        df = portfolio_h.reset_index().rename(columns={"index": "ts"})
        chart = alt.Chart(df).mark_line().encode(
            x=alt.X("ts:T", title="Date"),
            y=alt.Y("total_account_value:Q", title="Portfolio Value ($)"),
        ).properties(width=700, height=240, title="Equity curve")
        chart
    return


@app.cell
def _daily_summary(daily, mo, np, pd):
    if daily.empty or "daily_pct_return" not in daily.columns:
        mo.md("*No daily series.*")
    else:
        r = daily["daily_pct_return"].dropna()
        ann = r.mean() * 365.0
        ann_vol = r.std() * np.sqrt(365.0)
        sharpe = ann / ann_vol if ann_vol > 0 else float("nan")
        n_days = len(r)
        mo.md(
            f"**Daily stats over {n_days} days**\n\n"
            f"- Annualised return: **{ann:.2f}%**\n"
            f"- Annualised vol: **{ann_vol:.2f}%**\n"
            f"- Sharpe (rf=0): **{sharpe:.2f}**\n"
            f"- Best day: **{r.max():.2f}%**\n"
            f"- Worst day: **{r.min():.2f}%**\n"
        )
    return


@app.cell
def _daily_hist(alt, daily, mo, pd):
    if daily.empty:
        mo.md("")
    else:
        df = daily.reset_index().rename(columns={"index": "ts"})
        chart = alt.Chart(df).mark_bar().encode(
            x=alt.X("daily_pct_return:Q", bin=alt.Bin(maxbins=40), title="Daily % return"),
            y=alt.Y("count()", title="Days"),
        ).properties(width=600, height=200, title="Distribution of daily returns")
        chart
    return


@app.cell
def _sweep_panel(glob, mo, os, pd, run_picker):
    """If the loaded run is a sweep (has sibling __l<L>_a<A>_p<P> sub-runs),
    aggregate their final returns into a 3D table."""
    if run_picker is None:
        mo.md("")
        return

    base = run_picker.value
    # Sub-runs are named <base>__<COIN>__l<L>_a<A>_p<P>
    sibling_globs = sorted(glob.glob(f"backtest/runs/{base}__*"))
    if not sibling_globs:
        mo.md("*No sweep sub-runs detected for this run.*")
        return

    rows = []
    for sib in sibling_globs:
        sib_id = os.path.basename(sib)
        # Parse params out of the name
        try:
            tail = sib_id.split("__")[-1]
            parts = tail.split("_")
            lvl = int(parts[0][1:])
            alloc = int(parts[1][1:])
            pm = int(parts[2][1:])
        except Exception:
            continue
        agg_dir = f"{sib}/agg"
        try:
            ph = pd.read_parquet(f"{agg_dir}/portfolio_hourly.parquet")
            total_ret = float(ph["pct_return"].iloc[-1])
        except Exception:
            continue
        rows.append({"lvl": lvl, "alloc": alloc, "pm": pm, "return_pct": total_ret})

    sweep_df = pd.DataFrame(rows)
    return sibling_globs, sweep_df


@app.cell
def _sweep_heatmap(alt, mo, sweep_df):
    if sweep_df is None or sweep_df.empty:
        mo.md("*No sweep results to chart yet.*")
    else:
        # Average over pm dimension for a 2D heatmap
        agg = sweep_df.groupby(["lvl", "alloc"])["return_pct"].mean().reset_index()
        heat = alt.Chart(agg).mark_rect().encode(
            x=alt.X("lvl:O", title="trade_start_level"),
            y=alt.Y("alloc:O", title="start_allocation_pct"),
            color=alt.Color("return_pct:Q", title="Mean % return"),
            tooltip=["lvl", "alloc", "return_pct"],
        ).properties(width=500, height=300, title="Mean total return by (lvl, alloc), avg over pm")
        heat
    return


if __name__ == "__main__":
    app.run()
