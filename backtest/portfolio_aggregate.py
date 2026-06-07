"""
Daily portfolio aggregation with per-coin attribution.

Inputs:
  runs/<run_id>/fills.parquet       (multi-coin fill log)
  runs/<run_id>/series.parquet      (daily snapshots: cash + per-coin position_usd)

Output:
  runs/<run_id>/portfolio_daily.parquet  one row per snapshot day

Columns:
  ts, ts_iso, cash, total_position_usd, total_account_value,
  daily_pct_return,
  contrib_pct_<COIN>   ...one per active coin

Attribution math (zero-gap by construction)
-------------------------------------------
For each (coin, day):
  contrib_usd[c, d] = sell_notional[c, d]
                    - buy_notional[c, d]
                    + (position_usd[c, d] - position_usd[c, d-1])

Σ_c contrib_usd[c, d] ≡ Δ(cash + Σ position_usd)[d] = ΔV_total[d]

Reasoning:
  ΔV_total      = Δcash + Σ Δposition_usd[c]
  Δcash         = -Σ buy_notional + Σ sell_notional
  Therefore     ΔV_total = Σ (-buy + sell + Δposition_usd)
                         = Σ contrib_usd

Daily return:
  daily_pct_return[d]   = ΔV_total[d] / V_total[d-1] * 100
  contrib_pct[c, d]     = contrib_usd[c, d] / V_total[d-1] * 100

So Σ_c contrib_pct[c, d] = daily_pct_return[d] exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from . import workspace as ws


def _coin_from_symbol(sym: str) -> str:
    """'BTC_USD' or 'BTC_USDT' → 'BTC'."""
    return str(sym).split("_")[0].upper()


def attribute_daily(
    fills: pd.DataFrame,
    series: pd.DataFrame,
) -> pd.DataFrame:
    """Compute daily portfolio + per-coin contribution from raw fills + series.

    Returns a DataFrame indexed by snapshot ts with columns:
      cash, total_position_usd, total_account_value, daily_pct_return,
      contrib_pct_<COIN> ...
    """
    if series.empty:
        return pd.DataFrame()

    # Ensure series has ts as the index and is sorted.
    s = series.copy()
    if "ts" in s.columns:
        s = s.set_index("ts")
    s = s.sort_index()

    # Per-coin position_usd columns: anything matching 'position_usd_<COIN>'
    pos_cols = [c for c in s.columns if c.startswith("position_usd_")]
    coins = sorted(c.removeprefix("position_usd_") for c in pos_cols)

    # Δposition_usd per coin (NaN-safe: assume previous = 0 if NaN)
    pos = s[pos_cols].fillna(0.0)
    pos.columns = coins  # rename to bare coin tickers
    delta_pos = pos.diff().fillna(0.0)

    # Aggregate fills into per (date, coin, side) notionals
    buy_per_day = pd.DataFrame(0.0, index=s.index, columns=coins)
    sell_per_day = pd.DataFrame(0.0, index=s.index, columns=coins)
    if not fills.empty:
        f = fills.copy()
        if "ts" in f.columns:
            # The exchange stores ts as float Unix seconds. Coerce
            # explicitly — without unit="s" pandas treats the float as
            # nanoseconds and the fills all land in 1970.
            if pd.api.types.is_numeric_dtype(f["ts"]):
                f["ts"] = pd.to_datetime(f["ts"], unit="s", utc=True)
            else:
                f["ts"] = pd.to_datetime(f["ts"], utc=True)
        # Map each fill's ts to the *snapshot* day that contains it (i.e.
        # the next snapshot index strictly >= fill ts). Use searchsorted.
        f = f.sort_values("ts")
        snapshot_ts = s.index.to_numpy()
        # searchsorted right gives first index > value; we want the snapshot
        # that closes this fill's day, which is the one >= ts.
        idx_pos = pd.DatetimeIndex(s.index).searchsorted(
            pd.DatetimeIndex(f["ts"]), side="left",
        )
        # Clamp at len-1 for fills past the last snapshot
        idx_pos = idx_pos.clip(max=len(snapshot_ts) - 1)
        f["snapshot_ts"] = [snapshot_ts[i] for i in idx_pos]
        f["coin"] = f["symbol"].map(_coin_from_symbol)

        for (snap, coin), grp in f.groupby(["snapshot_ts", "coin"]):
            buys = float(grp.loc[grp["side"] == "buy", "notional"].sum())
            sells = float(grp.loc[grp["side"] == "sell", "notional"].sum())
            if coin in buy_per_day.columns:
                buy_per_day.at[snap, coin] = buys
                sell_per_day.at[snap, coin] = sells

    # Contribution in USD: sell - buy + Δposition
    contrib_usd = sell_per_day - buy_per_day + delta_pos

    # Portfolio totals
    total_v = s["total_account_value"].astype(float)
    prev_total = total_v.shift(1)
    daily_pct_return = ((total_v - prev_total) / prev_total * 100.0).fillna(0.0)

    # Per-coin % contribution (so sum across coins == daily_pct_return)
    contrib_pct = contrib_usd.div(prev_total, axis=0) * 100.0
    contrib_pct = contrib_pct.fillna(0.0)
    contrib_pct.columns = [f"contrib_pct_{c}" for c in contrib_pct.columns]

    out = pd.DataFrame({
        "cash": s["cash"].astype(float),
        "total_position_usd": s["total_position_usd"].astype(float),
        "total_account_value": total_v,
        "daily_pct_return": daily_pct_return,
    })
    out = pd.concat([out, contrib_pct], axis=1)
    out["ts_iso"] = out.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def attribution_residual(daily: pd.DataFrame) -> pd.Series:
    """Σ(contrib_pct) - daily_pct_return per day. Should be ~0 within
    float precision; values > 1e-6 % indicate an accounting bug."""
    contrib_cols = [c for c in daily.columns if c.startswith("contrib_pct_")]
    if not contrib_cols:
        return pd.Series(dtype=float)
    return daily[contrib_cols].sum(axis=1) - daily["daily_pct_return"]


def write_portfolio_daily(run_id: str) -> Optional[pd.DataFrame]:
    """Read fills + series from disk, compute attribution, write back."""
    run_dir = ws.run_dir(run_id)
    fills_path = run_dir / "fills.parquet"
    series_path = run_dir / "series.parquet"
    if not series_path.exists():
        return None
    series = pd.read_parquet(series_path)
    fills = (
        pd.read_parquet(fills_path) if fills_path.exists() else pd.DataFrame()
    )
    daily = attribute_daily(fills, series)
    if daily.empty:
        return daily
    out_path = run_dir / "portfolio_daily.parquet"
    daily.to_parquet(out_path)
    return daily
