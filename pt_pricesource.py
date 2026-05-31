"""
PriceSource abstraction — unified interface for OHLCV candle access.

Used by the backtest engine to swap between live KuCoin REST and historical
ArcticDB data without changing call sites. Production code is NOT yet wired
to this module — pt_thinker.py still calls KuCoin directly. Wiring prod to
LivePriceSource is a separate, validated step.

Conventions
-----------
- `coin` is the base symbol ("BTC"), not the pair.
- `tf_minutes` matches pt_env.TRAIN_TF_MINUTES + the kucoin5 opt-in:
  5, 60, 120, 240, 480, 720, 1440, 10080.
- Returned DataFrames are indexed by tz-aware UTC timestamp, oldest-first,
  with columns ["open", "high", "low", "close", "volume"].
- `asof_ts` (Unix seconds) excludes candles with index >= cutoff. Provides
  no-look-ahead semantics for backtests.

Usage
-----
    src = ArcticPriceSource()
    df  = src.get_candles("BTC", 60, asof_ts=1735689600, n_back=200)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

_OHLCV = ["open", "high", "low", "close", "volume"]


class PriceSource(ABC):
    """Abstract OHLCV candle provider."""

    @abstractmethod
    def get_candles(
        self,
        coin: str,
        tf_minutes: int,
        asof_ts: Optional[float] = None,
        n_back: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Return OHLCV candles for (coin, tf_minutes).

        Args:
            coin:        Base symbol, e.g. "BTC".
            tf_minutes:  Timeframe in minutes.
            asof_ts:     Unix seconds. If set, exclude candles with
                         timestamp >= asof_ts (no-look-ahead cutoff).
            n_back:      If set, return only the most recent n_back rows
                         after asof_ts filtering.

        Returns:
            DataFrame indexed by tz-aware UTC timestamp, oldest-first,
            with columns ["open", "high", "low", "close", "volume"].
        """
        ...


class ArcticPriceSource(PriceSource):
    """
    Reads OHLCV from a local ArcticDB lmdb store.

    Defaults to the shared `~/dev/data/arcticdb` store, which holds
    `kucoin{tf}` libraries with `<COIN>_USDT` symbols. Used by the
    backtest engine to feed historical candles into the trainer,
    thinker scoring, and 5min fill-grid.
    """

    def __init__(
        self,
        arctic_url: Optional[str] = None,
        lib_prefix: str = "kucoin",
        denominator: str = "USDT",
    ):
        import arcticdb as adb
        import getpass

        if arctic_url is None:
            arctic_url = f"lmdb:///home/{getpass.getuser()}/dev/data/arcticdb"
        self._arctic = adb.Arctic(arctic_url)
        self._prefix = lib_prefix
        self._denom = denominator
        self._lib_cache: dict = {}

    def _get_lib(self, tf_minutes: int):
        lib_name = f"{self._prefix}{tf_minutes}"
        if lib_name not in self._lib_cache:
            if lib_name not in self._arctic.list_libraries():
                raise KeyError(f"ArcticDB library {lib_name!r} not found")
            self._lib_cache[lib_name] = self._arctic.get_library(lib_name)
        return self._lib_cache[lib_name]

    def get_candles(
        self,
        coin: str,
        tf_minutes: int,
        asof_ts: Optional[float] = None,
        n_back: Optional[int] = None,
    ) -> pd.DataFrame:
        lib = self._get_lib(tf_minutes)
        symbol = f"{coin.upper()}_{self._denom}"

        if asof_ts is not None:
            # Use ArcticDB date_range to push the cutoff to the store layer
            # (avoids loading post-cutoff rows we'd just drop).
            cutoff = pd.Timestamp(asof_ts, unit="s", tz="UTC") - pd.Timedelta(microseconds=1)
            df = lib.read(symbol, date_range=(None, cutoff)).data
        else:
            df = lib.read(symbol).data

        if df is None or df.empty:
            return pd.DataFrame(columns=_OHLCV).rename_axis("timestamp")

        # Normalize: lower-case, OHLCV subset, oldest-first
        df.columns = [c.lower() for c in df.columns]
        missing = [c for c in _OHLCV if c not in df.columns]
        if missing:
            raise ValueError(
                f"{symbol}/{self._prefix}{tf_minutes} missing columns: {missing}"
            )
        df = df[_OHLCV].sort_index()

        if n_back is not None:
            df = df.tail(n_back)
        return df


class LivePriceSource(PriceSource):
    """
    KuCoin REST kline source — for future prod integration.

    NOT yet wired into pt_thinker.py. Validation against the existing
    thinker's parsing will happen when we integrate.

    Does NOT support `asof_ts` — KuCoin always returns "as of now".
    """

    _TF_MAP = {
        1: "1min",   5: "5min",   15: "15min",  30: "30min",
        60: "1hour", 120: "2hour", 240: "4hour", 480: "8hour",
        720: "12hour", 1440: "1day", 10080: "1week",
    }

    def __init__(self, kucoin_url: str = "https://api.kucoin.com"):
        from kucoin.client import Market
        self._market = Market(url=kucoin_url)

    def get_candles(
        self,
        coin: str,
        tf_minutes: int,
        asof_ts: Optional[float] = None,
        n_back: Optional[int] = None,
    ) -> pd.DataFrame:
        if asof_ts is not None:
            raise NotImplementedError(
                "LivePriceSource does not support asof_ts cutoff — KuCoin is live-only"
            )
        if tf_minutes not in self._TF_MAP:
            raise ValueError(f"Unsupported tf_minutes={tf_minutes} for KuCoin")

        kucoin_tf = self._TF_MAP[tf_minutes]
        pair = f"{coin.upper()}-USDT"
        raw = self._market.get_kline(pair, kucoin_tf)

        # KuCoin order: [time, open, close, high, low, volume, turnover]; newest-first
        rows = []
        for r in raw or []:
            try:
                rows.append((
                    int(r[0]),
                    float(r[1]),  # open
                    float(r[3]),  # high
                    float(r[4]),  # low
                    float(r[2]),  # close
                    float(r[5]),  # volume
                ))
            except (IndexError, ValueError, TypeError):
                continue

        if not rows:
            return pd.DataFrame(columns=_OHLCV).rename_axis("timestamp")

        df = pd.DataFrame(rows, columns=["timestamp"] + _OHLCV)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.set_index("timestamp").sort_index()

        if n_back is not None:
            df = df.tail(n_back)
        return df
