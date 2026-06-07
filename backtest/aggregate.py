"""
Aggregate per-coin backtest outputs into the requested timeseries:
  - hourly $pnl, $invested, $total_account_value, % return per coin
  - portfolio-level rollups
  - daily % return series

Inputs: runs/<run_id>/fills/<COIN>.parquet + series/<COIN>.parquet (engine output).
Outputs: runs/<run_id>/agg/<COIN>_hourly.parquet, portfolio_hourly.parquet, portfolio_daily.parquet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import workspace as ws


_TS_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _with_ts_iso(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a `ts_iso` column derived from the DatetimeIndex.

    Lets parquet viewers (DuckDB, parquet-tools, the VS Code parquet
    extension etc.) show human-readable timestamps without needing the
    pandas roundtrip. The native DatetimeIndex is unchanged so all
    downstream pandas usage keeps working.
    """
    if df.empty:
        return df
    out = df.copy()
    idx = out.index
    if hasattr(idx, "strftime"):
        out["ts_iso"] = idx.strftime(_TS_ISO_FMT)
    return out


def per_coin_hourly(series: pd.DataFrame) -> pd.DataFrame:
    """Resample engine snapshots (already hourly if record_every_n=12) into a
    clean hourly series with derived columns:
      live_price, qty, position_usd, cash, total_account_value,
      invested_usd (= position_usd), pnl_usd (= total - starting), pct_return.
    Index is hourly UTC."""
    if series is None or series.empty:
        return pd.DataFrame()
    df = series.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index("ts")
    df = df.sort_index()

    # Resample to hourly using 'last' so partial-bar artefacts don't double-count
    out = df.resample("1h").last().ffill()
    out["invested_usd"] = out["position_usd"]

    start_total = float(out["total_account_value"].iloc[0])
    out["pnl_usd"] = out["total_account_value"] - start_total
    out["pct_return"] = (out["total_account_value"] / start_total - 1.0) * 100.0
    return out


def write_per_coin_hourly(run_id: str, coin: str) -> Optional[pd.DataFrame]:
    series_path = ws.run_dir(run_id) / "series" / f"{coin}.parquet"
    if not series_path.exists():
        return None
    series = pd.read_parquet(series_path)
    hourly = per_coin_hourly(series)
    if hourly.empty:
        return hourly
    out_dir = ws.ensure_dir(ws.run_dir(run_id) / "agg")
    _with_ts_iso(hourly).to_parquet(out_dir / f"{coin}_hourly.parquet")
    return hourly


def portfolio_hourly(run_id: str, coins: list[str]) -> pd.DataFrame:
    """Sum per-coin hourly into a portfolio-level series.

    Each coin runs with its own starting allocation (typically $1000); the
    portfolio total is the sum. pct_return is portfolio-weighted.
    """
    frames = []
    for coin in coins:
        h = write_per_coin_hourly(run_id, coin)
        if h is None or h.empty:
            continue
        h = h[["total_account_value", "invested_usd", "pnl_usd"]].copy()
        h.columns = pd.MultiIndex.from_product([[coin], h.columns])
        frames.append(h)

    if not frames:
        return pd.DataFrame()

    wide = pd.concat(frames, axis=1).sort_index().ffill()

    portfolio = pd.DataFrame(index=wide.index)
    portfolio["total_account_value"] = (
        wide.xs("total_account_value", axis=1, level=1).sum(axis=1)
    )
    portfolio["invested_usd"] = (
        wide.xs("invested_usd", axis=1, level=1).sum(axis=1)
    )
    portfolio["pnl_usd"] = (
        wide.xs("pnl_usd", axis=1, level=1).sum(axis=1)
    )
    start = float(portfolio["total_account_value"].iloc[0])
    portfolio["pct_return"] = (portfolio["total_account_value"] / start - 1.0) * 100.0

    out_dir = ws.ensure_dir(ws.run_dir(run_id) / "agg")
    _with_ts_iso(portfolio).to_parquet(out_dir / "portfolio_hourly.parquet")
    _with_ts_iso(wide).to_parquet(out_dir / "portfolio_wide_hourly.parquet")
    return portfolio


def portfolio_daily(run_id: str, portfolio: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Daily returns for downstream statistical analysis."""
    if portfolio is None:
        path = ws.run_dir(run_id) / "agg" / "portfolio_hourly.parquet"
        if not path.exists():
            return pd.DataFrame()
        portfolio = pd.read_parquet(path)
    if portfolio.empty:
        return pd.DataFrame()
    daily = portfolio.resample("1d").last().ffill()
    daily["daily_pct_return"] = daily["total_account_value"].pct_change() * 100.0
    out_dir = ws.ensure_dir(ws.run_dir(run_id) / "agg")
    _with_ts_iso(daily).to_parquet(out_dir / "portfolio_daily.parquet")
    return daily
