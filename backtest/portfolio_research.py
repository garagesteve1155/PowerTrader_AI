"""Marimo notebook for the joint multi-coin backtest.

Reads runs/<run_id>/portfolio_daily.parquet (produced by
backtest.portfolio_aggregate.write_portfolio_daily) and lays out:

  1. Run picker (only shows runs with portfolio_daily.parquet)
  2. Headline metrics (return, vol, Sharpe, max DD, residual)
  3. Total portfolio value (daily, mark-to-market) — the real curve
  4. Cash vs invested (stacked area) — shows buying-power exhaustion
  5. Per-coin position_usd over time — one line per coin
  6. Per-coin cumulative %-return contribution (sums to total return)
  7. Daily portfolio %-return — bars
  8. Daily-return histogram
  9. Attribution residual diagnostic (should be ~0 always)

All charts use Plotly. All quantities are at the daily snapshot
resolution defined by the engine.
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

    RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")


@app.cell
def _run_picker():
    """Only list runs that have the joint-engine artifact."""
    candidates = sorted(glob.glob(os.path.join(RUNS_DIR, "*")))
    run_ids = []
    for p in candidates:
        if not os.path.isdir(p):
            continue
        if not os.path.exists(os.path.join(p, "portfolio_daily.parquet")):
            continue
        run_ids.append(os.path.basename(p))

    if run_ids:
        run_picker = mo.ui.dropdown(
            options=run_ids, value=run_ids[-1], label="Run ID",
        )
        run_picker_view = run_picker
    else:
        run_picker = None
        run_picker_view = mo.md(
            f"**No joint-engine runs found in `{RUNS_DIR}`.**  \n"
            f"Run a joint backtest first, then "
            "`portfolio_aggregate.write_portfolio_daily(run_id)`."
        )
    run_picker_view
    return (run_picker,)


@app.cell
def _load(run_picker):
    if run_picker is None:
        daily = pd.DataFrame()
        series = pd.DataFrame()
        fills = pd.DataFrame()
        run_id = None
    else:
        run_id = run_picker.value
        rd = os.path.join(RUNS_DIR, run_id)
        try:
            daily = pd.read_parquet(os.path.join(rd, "portfolio_daily.parquet"))
        except (FileNotFoundError, OSError):
            daily = pd.DataFrame()
        try:
            series = pd.read_parquet(os.path.join(rd, "series.parquet"))
            if "ts" in series.columns:
                series = series.set_index("ts").sort_index()
        except (FileNotFoundError, OSError):
            series = pd.DataFrame()
        try:
            fills = pd.read_parquet(os.path.join(rd, "fills.parquet"))
        except (FileNotFoundError, OSError):
            fills = pd.DataFrame()
    return daily, fills, run_id, series


@app.cell
def _coin_list(daily):
    contrib_cols = [c for c in daily.columns if c.startswith("contrib_pct_")]
    coins = [c.removeprefix("contrib_pct_") for c in contrib_cols]
    return coins, contrib_cols


@app.cell
def _headline(coins, contrib_cols, daily, fills, run_id):
    if daily.empty:
        headline = mo.md(
            "*No `portfolio_daily.parquet` for this run yet — run "
            "`portfolio_aggregate.write_portfolio_daily(run_id)` first.*"
        )
    else:
        _start = float(daily["total_account_value"].iloc[0])
        _last = float(daily["total_account_value"].iloc[-1])
        _ret_compound = (_last / _start - 1.0) * 100.0 if _start > 0 else 0.0
        _r = daily["daily_pct_return"].dropna()
        _n_days = len(_r)
        _ann = _r.mean() * 365.0 if _n_days else 0.0
        _ann_vol = _r.std() * np.sqrt(365.0) if _n_days else 0.0
        _sharpe = _ann / _ann_vol if _ann_vol > 0 else float("nan")
        _cum = daily["total_account_value"]
        _peak = _cum.cummax()
        _dd = (_cum / _peak - 1.0) * 100.0
        _max_dd = float(_dd.min()) if len(_dd) else 0.0
        _start_date = daily.index[0].strftime("%Y-%m-%d")
        _end_date = daily.index[-1].strftime("%Y-%m-%d")
        _n_fills = len(fills)
        _n_buys = int((fills["side"] == "buy").sum()) if not fills.empty else 0
        _n_sells = int((fills["side"] == "sell").sum()) if not fills.empty else 0
        # Attribution residual (should be ~0)
        _resid = (
            daily[contrib_cols].sum(axis=1) - daily["daily_pct_return"]
        ).abs().max() if contrib_cols else float("nan")
        headline = mo.md(
            f"### Run `{run_id}` — {_n_days} days, {len(coins)} coins\n\n"
            f"**{_start_date} → {_end_date}**\n\n"
            f"- Starting portfolio: **${_start:,.2f}**\n"
            f"- Final portfolio:    **${_last:,.2f}**  ({_ret_compound:+.2f}% compound)\n"
            f"- Annualised return:  **{_ann:+.2f}%**\n"
            f"- Annualised vol:     **{_ann_vol:.2f}%**\n"
            f"- Sharpe (rf=0):      **{_sharpe:.2f}**\n"
            f"- Max drawdown:       **{_max_dd:.2f}%**\n"
            f"- Fills: **{_n_fills}** ({_n_buys} buys, {_n_sells} sells)\n"
            f"- Attribution residual (should be ≈0): **{_resid:.2e} %**\n"
        )
    headline
    return


@app.cell
def _portfolio_value(daily):
    if daily.empty:
        fig_v = mo.md("")
    else:
        fig_v = go.Figure()
        fig_v.add_trace(go.Scatter(
            x=daily.index, y=daily["total_account_value"],
            mode="lines", name="Total account value",
            line=dict(color="#1f77b4", width=2),
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.2f}<extra></extra>",
        ))
        fig_v.update_layout(
            title="Portfolio value (daily, mark-to-market)",
            xaxis_title="Date", yaxis_title="USD",
            height=320, margin=dict(l=60, r=20, t=50, b=40),
            hovermode="x unified",
        )
    fig_v
    return


@app.cell
def _cash_vs_invested(daily):
    """Stacked area: cash + total_position_usd = total_account_value.

    When the cash band shrinks to zero, the trader has fully deployed
    its buying power — useful for spotting DCA-induced exhaustion.
    """
    if daily.empty:
        fig_ci = mo.md("")
    else:
        fig_ci = go.Figure()
        fig_ci.add_trace(go.Scatter(
            x=daily.index, y=daily["cash"],
            mode="lines", name="Cash",
            stackgroup="one",
            line=dict(color="#2ca02c", width=0),
            fillcolor="rgba(44,160,44,0.55)",
            hovertemplate="Cash: $%{y:,.0f}<extra></extra>",
        ))
        fig_ci.add_trace(go.Scatter(
            x=daily.index, y=daily["total_position_usd"],
            mode="lines", name="Position value",
            stackgroup="one",
            line=dict(color="#ff7f0e", width=0),
            fillcolor="rgba(255,127,14,0.55)",
            hovertemplate="Positions: $%{y:,.0f}<extra></extra>",
        ))
        fig_ci.update_layout(
            title="Cash vs invested — stacked area sums to total account value",
            xaxis_title="Date", yaxis_title="USD",
            height=280, margin=dict(l=60, r=20, t=50, b=40),
            hovermode="x unified",
        )
    fig_ci
    return


@app.cell
def _per_coin_position(coins, series):
    """One line per coin: position_usd_<COIN> over time."""
    if series.empty or not coins:
        fig_pc = mo.md("")
    else:
        fig_pc = go.Figure()
        for _c in sorted(coins):
            _col = f"position_usd_{_c}"
            if _col not in series.columns:
                continue
            fig_pc.add_trace(go.Scatter(
                x=series.index, y=series[_col],
                mode="lines", name=_c,
                hovertemplate=f"<b>{_c}</b><br>%{{x|%Y-%m-%d}}<br>$%{{y:,.2f}}<extra></extra>",
            ))
        fig_pc.update_layout(
            title="Per-coin position value over time (mark-to-market USD)",
            xaxis_title="Date", yaxis_title="USD invested in coin",
            height=420, margin=dict(l=60, r=20, t=50, b=40),
            legend=dict(orientation="v", x=1.02, y=1),
            hovermode="closest",
        )
    fig_pc
    return


@app.cell
def _per_coin_cumulative_contrib(coins, contrib_cols, daily):
    """Cumulative per-coin %-contribution (additive form).

    Σ_c cumulative_contrib[c] = sum_of_daily_pct (the additive return).
    Each line shows that coin's running share of the portfolio's
    additive return through time.
    """
    if daily.empty or not contrib_cols:
        fig_cc = mo.md("")
    else:
        _cum = daily[contrib_cols].cumsum()
        fig_cc = go.Figure()
        for _c, _col in zip(sorted(coins), sorted(contrib_cols)):
            fig_cc.add_trace(go.Scatter(
                x=_cum.index, y=_cum[_col],
                mode="lines", name=_c,
                hovertemplate=f"<b>{_c}</b><br>%{{x|%Y-%m-%d}}<br>%{{y:+.2f}}%<extra></extra>",
            ))
        # Add the additive total as a reference line.
        fig_cc.add_trace(go.Scatter(
            x=_cum.index, y=_cum.sum(axis=1),
            mode="lines", name="ALL (additive total)",
            line=dict(color="black", width=2, dash="dash"),
            hovertemplate="Total: %{y:+.2f}%<extra></extra>",
        ))
        fig_cc.update_layout(
            title="Cumulative per-coin %-return contribution "
                  "(sums to additive total — see BACKTEST.md §1.7)",
            xaxis_title="Date", yaxis_title="Cumulative % contribution",
            height=420, margin=dict(l=60, r=20, t=50, b=40),
            legend=dict(orientation="v", x=1.02, y=1),
            hovermode="closest",
        )
    fig_cc
    return


@app.cell
def _daily_return_ts(daily):
    if daily.empty:
        fig_dr = mo.md("")
    else:
        _r = daily["daily_pct_return"]
        fig_dr = go.Figure()
        fig_dr.add_trace(go.Bar(
            x=daily.index, y=_r.values, name="Daily % return",
            marker=dict(
                color=["#d62728" if v < 0 else "#2ca02c" for v in _r.values],
                line=dict(width=0),
            ),
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:+.3f}%<extra></extra>",
        ))
        fig_dr.update_layout(
            title="Daily portfolio %-return",
            xaxis_title="Date", yaxis_title="%",
            height=240, margin=dict(l=60, r=20, t=50, b=40),
            bargap=0,
        )
    fig_dr
    return


@app.cell
def _daily_return_hist(daily):
    if daily.empty:
        fig_hist = mo.md("")
    else:
        _r = daily["daily_pct_return"].dropna()
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(
            x=_r.values, nbinsx=60, name="Daily % return",
            marker=dict(color="#1f77b4"),
        ))
        fig_hist.update_layout(
            title="Distribution of daily %-returns",
            xaxis_title="%-return", yaxis_title="Days",
            height=260, margin=dict(l=60, r=20, t=50, b=40),
        )
    fig_hist
    return


@app.cell
def _attribution_diagnostic(contrib_cols, daily):
    """Per-day residual: Σ contrib_pct − daily_pct_return.

    Should be ~0 within float precision. A non-trivial residual is a
    bug in either the engine snapshot or the aggregator math.
    """
    if daily.empty or not contrib_cols:
        fig_resid = mo.md("")
    else:
        _resid = daily[contrib_cols].sum(axis=1) - daily["daily_pct_return"]
        fig_resid = go.Figure()
        fig_resid.add_trace(go.Scatter(
            x=_resid.index, y=_resid.values,
            mode="lines", name="Σ contrib − daily_return",
            line=dict(color="#9467bd", width=1),
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2e} %<extra></extra>",
        ))
        fig_resid.update_layout(
            title=(
                f"Attribution residual (max |resid| = "
                f"{_resid.abs().max():.2e} %; should be ≈0)"
            ),
            xaxis_title="Date", yaxis_title="% residual",
            height=200, margin=dict(l=60, r=20, t=50, b=40),
        )
    fig_resid
    return


if __name__ == "__main__":
    app.run()
