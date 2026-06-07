"""Marimo notebook for backtest research.

Layout
------
1. Run picker
2. Headline metrics (final return, vol, Sharpe, max DD)
3. Total portfolio value (daily, mark-to-market)
4. Total $ invested over time (sum of position_usd across coins)
5. Per-coin equity (% of portfolio starting capital, daily)
6. Per-coin invested ($, daily)
7. Daily-return histogram + time series
8. Sweep heatmap (only fills when sub-runs exist)

All analysis is at daily resolution. portfolio_daily.parquet is loaded
directly from disk; per-coin hourly parquets are resampled to daily
once at load time (the hourly data is kept on disk for precise audit
trail, but the notebook only displays daily-level quantities).

All charts use Plotly.
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


with app.setup:
    import glob
    import os

    import marimo as mo
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go

    # Resolve runs dir from THIS notebook's location, so the picker
    # works regardless of CWD (browser `marimo edit` vs the VS Code
    # extension launch their kernel from different places).
    RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
    STARTING_USD_PER_COIN = 1000.0   # matches engine.CoinRunConfig default


@app.cell
def _run_picker():
    runs = sorted(glob.glob(os.path.join(RUNS_DIR, "*")))
    run_ids = [os.path.basename(p) for p in runs if os.path.isdir(p)]
    if run_ids:
        run_picker = mo.ui.dropdown(options=run_ids, value=run_ids[-1], label="Run ID")
        run_picker_view = run_picker
    else:
        run_picker = None
        run_picker_view = mo.md(
            f"**No backtest runs found in `{RUNS_DIR}`.**  \n"
            f"Run `python3 -m backtest.cli pilot --coin ETH` first."
        )
    run_picker_view
    return (run_picker,)


@app.cell
def _load(run_picker):
    """Load portfolio_daily + per-coin frames (resampled to daily).

    The notebook works entirely at daily resolution. The portfolio
    parquet is loaded straight from disk; per-coin parquets are
    resampled once here so every downstream chart shares one time axis
    and Plotly stays fast.
    """
    if run_picker is None:
        daily = pd.DataFrame()
        per_coin_d = {}
        run_id = None
        n_coins_with_data = 0
    else:
        run_id = run_picker.value
        _agg_dir = os.path.join(RUNS_DIR, run_id, "agg")
        try:
            daily = pd.read_parquet(
                os.path.join(_agg_dir, "portfolio_daily.parquet"))
        except (FileNotFoundError, OSError):
            daily = pd.DataFrame()

        per_coin_d: dict[str, pd.DataFrame] = {}
        for path in sorted(glob.glob(os.path.join(_agg_dir, "*_hourly.parquet"))):
            base = os.path.basename(path).removesuffix("_hourly.parquet")
            if base.startswith("portfolio"):
                continue
            try:
                _h = pd.read_parquet(path)
                per_coin_d[base] = _h.resample("1D").last().dropna(how="all")
            except Exception:
                continue
        n_coins_with_data = len(per_coin_d)
    return daily, n_coins_with_data, per_coin_d, run_id


@app.cell
def _headline(daily, n_coins_with_data, run_id):
    if daily.empty:
        headline = mo.md(
            "*No `portfolio_daily.parquet` yet — run "
            "`python3 -m backtest.cli aggregate <run_id>` first.*"
        )
    else:
        _start = float(daily["total_account_value"].iloc[0])
        _last = float(daily["total_account_value"].iloc[-1])
        _ret = (_last / _start - 1.0) * 100.0 if _start > 0 else 0.0
        _r = daily["daily_pct_return"].dropna()
        _n_days = len(_r)
        _ann = _r.mean() * 365.0 if _n_days else 0.0
        _ann_vol = _r.std() * np.sqrt(365.0) if _n_days else 0.0
        _sharpe = _ann / _ann_vol if _ann_vol > 0 else float("nan")
        _cum = daily["total_account_value"]
        _peak = _cum.cummax()
        _dd_series = (_cum / _peak - 1.0) * 100.0
        _max_dd = float(_dd_series.min()) if len(_dd_series) else 0.0
        _start_date = daily.index[0].strftime("%Y-%m-%d")
        _end_date = daily.index[-1].strftime("%Y-%m-%d")
        headline = mo.md(
            f"### Run `{run_id}` — {_n_days} days, {n_coins_with_data} coins\n\n"
            f"**{_start_date} → {_end_date}**\n\n"
            f"- Starting portfolio: **${_start:,.2f}**\n"
            f"- Final portfolio:    **${_last:,.2f}**  ({_ret:+.2f}%)\n"
            f"- Annualised return:  **{_ann:+.2f}%**\n"
            f"- Annualised vol:     **{_ann_vol:.2f}%**\n"
            f"- Sharpe (rf=0):      **{_sharpe:.2f}**\n"
            f"- Max drawdown:       **{_max_dd:.2f}%**\n"
        )
    headline
    return


@app.cell
def _portfolio_value(daily):
    if daily.empty:
        portfolio_fig = mo.md("")
    else:
        portfolio_fig = go.Figure()
        portfolio_fig.add_trace(go.Scatter(
            x=daily.index, y=daily["total_account_value"],
            mode="lines", name="Total account value",
            line=dict(color="#1f77b4", width=2),
        ))
        portfolio_fig.update_layout(
            title="Portfolio value (daily, mark-to-market)",
            xaxis_title="Date", yaxis_title="USD",
            height=320, margin=dict(l=60, r=20, t=50, b=40),
            hovermode="x unified",
        )
    portfolio_fig
    return


@app.cell
def _total_invested(per_coin_d):
    """Sum of position_usd across all coins, daily."""
    if not per_coin_d:
        invested_fig = mo.md("")
    else:
        _invested = pd.concat(
            {_c: _df["position_usd"] for _c, _df in per_coin_d.items()},
            axis=1,
        ).sum(axis=1)
        invested_fig = go.Figure()
        invested_fig.add_trace(go.Scatter(
            x=_invested.index, y=_invested.values,
            mode="lines", name="Total invested",
            fill="tozeroy",
            line=dict(color="#2ca02c", width=1.5),
        ))
        invested_fig.update_layout(
            title="Total $ invested across all coins (daily)",
            xaxis_title="Date", yaxis_title="USD",
            height=280, margin=dict(l=60, r=20, t=50, b=40),
            hovermode="x unified",
        )
    invested_fig
    return


@app.cell
def _per_coin_equity(per_coin_d):
    """One line per coin: total_account_value as % of portfolio start.

    Denominator = N_coins × STARTING_USD_PER_COIN (portfolio's combined
    starting capital), so each line represents that coin's contribution
    to the portfolio measured against the total starting bankroll.
    """
    if not per_coin_d:
        coin_equity_fig = mo.md("")
    else:
        _denom = STARTING_USD_PER_COIN * len(per_coin_d)
        coin_equity_fig = go.Figure()
        for _c, _df in sorted(per_coin_d.items()):
            coin_equity_fig.add_trace(go.Scatter(
                x=_df.index,
                y=_df["total_account_value"] / _denom * 100.0,
                mode="lines", name=_c,
                hovertemplate=f"<b>{_c}</b><br>%{{x|%Y-%m-%d}}<br>%{{y:.3f}}%<extra></extra>",
            ))
        coin_equity_fig.update_layout(
            title=f"Per-coin equity as % of portfolio starting capital "
                  f"(${_denom:,.0f})",
            xaxis_title="Date", yaxis_title="% of portfolio start",
            height=420, margin=dict(l=60, r=20, t=50, b=40),
            legend=dict(orientation="v", x=1.02, y=1),
            hovermode="closest",
        )
    coin_equity_fig
    return


@app.cell
def _per_coin_invested(per_coin_d):
    """One line per coin: position_usd (the $ mark-to-market exposure)."""
    if not per_coin_d:
        coin_invested_fig = mo.md("")
    else:
        coin_invested_fig = go.Figure()
        for _c, _df in sorted(per_coin_d.items()):
            coin_invested_fig.add_trace(go.Scatter(
                x=_df.index, y=_df["position_usd"],
                mode="lines", name=_c,
                hovertemplate=f"<b>{_c}</b><br>%{{x|%Y-%m-%d}}<br>$%{{y:,.2f}}<extra></extra>",
            ))
        coin_invested_fig.update_layout(
            title="Per-coin invested $ (position_usd, daily)",
            xaxis_title="Date", yaxis_title="USD invested",
            height=420, margin=dict(l=60, r=20, t=50, b=40),
            legend=dict(orientation="v", x=1.02, y=1),
            hovermode="closest",
        )
    coin_invested_fig
    return


@app.cell
def _daily_return_ts(daily):
    if daily.empty or "daily_pct_return" not in daily.columns:
        daily_ts_fig = mo.md("")
    else:
        _r = daily["daily_pct_return"].dropna()
        daily_ts_fig = go.Figure()
        daily_ts_fig.add_trace(go.Bar(
            x=_r.index, y=_r.values, name="Daily % return",
            marker=dict(
                color=["#d62728" if v < 0 else "#2ca02c" for v in _r.values],
                line=dict(width=0),
            ),
        ))
        daily_ts_fig.update_layout(
            title="Daily portfolio % return",
            xaxis_title="Date", yaxis_title="% return",
            height=240, margin=dict(l=60, r=20, t=50, b=40),
            bargap=0,
        )
    daily_ts_fig
    return


@app.cell
def _daily_return_hist(daily):
    if daily.empty or "daily_pct_return" not in daily.columns:
        hist_fig = mo.md("")
    else:
        _r = daily["daily_pct_return"].dropna()
        hist_fig = go.Figure()
        hist_fig.add_trace(go.Histogram(
            x=_r.values, nbinsx=60, name="Daily % return",
            marker=dict(color="#1f77b4"),
        ))
        hist_fig.update_layout(
            title="Distribution of daily % returns",
            xaxis_title="% return", yaxis_title="Days",
            height=260, margin=dict(l=60, r=20, t=50, b=40),
        )
    hist_fig
    return


@app.cell
def _sweep_panel(run_id):
    """If the loaded run is a sweep, gather sub-run final returns."""
    sibling_globs = (
        sorted(glob.glob(os.path.join(RUNS_DIR, f"{run_id}__*")))
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
        _sweep_agg_dir = os.path.join(sib, "agg")
        try:
            _ph = pd.read_parquet(
                os.path.join(_sweep_agg_dir, "portfolio_hourly.parquet"))
            _total_ret = float(_ph["pct_return"].iloc[-1])
        except (FileNotFoundError, OSError, KeyError, IndexError):
            continue
        rows.append({"lvl": lvl, "alloc": alloc, "pm": pm, "return_pct": _total_ret})

    sweep_df = pd.DataFrame(rows)
    return (sweep_df,)


@app.cell
def _sweep_heatmap(sweep_df):
    if sweep_df.empty:
        sweep_fig = mo.md("*No sweep sub-runs detected for this run.*")
    else:
        _agg = (
            sweep_df.groupby(["lvl", "alloc"])["return_pct"]
            .mean().reset_index()
        )
        _piv = _agg.pivot(index="alloc", columns="lvl", values="return_pct")
        sweep_fig = go.Figure(go.Heatmap(
            z=_piv.values, x=_piv.columns, y=_piv.index,
            colorscale="RdYlGn", zmid=0,
            colorbar=dict(title="% return"),
            hovertemplate="lvl=%{x}<br>alloc=%{y}<br>return=%{z:.2f}%<extra></extra>",
        ))
        sweep_fig.update_layout(
            title="Mean total return by (lvl, alloc), avg over pm",
            xaxis_title="trade_start_level",
            yaxis_title="start_allocation_pct",
            height=320, margin=dict(l=60, r=20, t=50, b=40),
        )
    sweep_fig
    return


if __name__ == "__main__":
    app.run()
