"""
Frictionless simulated exchange for the backtest engine.

Plugs into the prod Exchange ABC so BacktestTrader can use the unchanged
Trader logic for accounting, order placement wrappers, etc. The engine
calls `set_time(now_ts)` and `set_fill_price(coin, fill_price)` between
bars so all subsequent get_price / place_* calls reflect the new bar.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Dict, List, Optional, Tuple

from exchange_api import Exchange, OrderResult


def _ts_iso(ts_seconds: float) -> str:
    """Render a Unix-seconds float as PowerTrader's canonical UTC ISO string."""
    return _dt.datetime.fromtimestamp(
        float(ts_seconds), _dt.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


class BacktestExchange(Exchange):
    """Mid-grid frictionless fills, no fees, no spread.

    Convention: fill_price for a step is the *close* of the in-progress
    5min bar (decision was at the bar's open). Mid-grid in the
    backtest's "decide at open, fill at close" model.
    """

    def __init__(self, starting_usd: float, denominator: str = "USD"):
        self._denom = denominator
        self._cash: float = float(starting_usd)
        self._holdings: Dict[str, float] = {}        # base -> qty
        self._fill_prices: Dict[str, float] = {}     # base -> fill price (5min close)
        self._bar_open_prices: Dict[str, float] = {} # base -> 5min open (used as live "current" price)
        self._orders_log: List[dict] = []
        self._now_ts: float = 0.0

    # ------------------------------------------------------------------
    # Engine plumbing (not part of the prod Exchange interface)
    # ------------------------------------------------------------------

    def set_time(self, now_ts: float) -> None:
        self._now_ts = float(now_ts)

    def set_bar(self, coin: str, bar_open: float, bar_close: float) -> None:
        base = coin.upper()
        self._bar_open_prices[base] = float(bar_open)
        self._fill_prices[base] = float(bar_close)

    def orders_log(self) -> List[dict]:
        return list(self._orders_log)

    # ------------------------------------------------------------------
    # Checkpoint serialization (for resumable runs)
    # ------------------------------------------------------------------

    def to_state(self) -> dict:
        """Pickle-friendly snapshot of all persistent state."""
        return {
            "denom":      self._denom,
            "cash":       self._cash,
            "holdings":   dict(self._holdings),
            "orders_log": list(self._orders_log),
        }

    def load_state(self, s: dict) -> None:
        """Restore from a `to_state()` dict. Per-bar fields stay transient."""
        self._denom = s.get("denom", "USD")
        self._cash = float(s["cash"])
        self._holdings = dict(s["holdings"])
        self._orders_log = list(s["orders_log"])

    # ------------------------------------------------------------------
    # Required Exchange methods
    # ------------------------------------------------------------------

    @property
    def key(self) -> str:
        return "backtest"

    @property
    def display_name(self) -> str:
        return "Backtest"

    @property
    def is_paper(self) -> bool:
        return True

    def get_account_value(self) -> Optional[float]:
        equity = self._cash
        for base, qty in self._holdings.items():
            price = self._bar_open_prices.get(base)
            if price is not None:
                equity += qty * price
        return equity

    def get_buying_power(self) -> Optional[float]:
        return self._cash

    def get_holdings(self) -> Dict[str, float]:
        # Filter zero/dust positions like prod paper does (1e-12 threshold)
        return {k: v for k, v in self._holdings.items() if v > 1e-12}

    def get_price(
        self, symbols: List[str]
    ) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        """Return mid-grid: buy_price == sell_price == 5min bar open
        (the "live" current price at decision time)."""
        buys: Dict[str, float] = {}
        sells: Dict[str, float] = {}
        valid: List[str] = []
        for sym in symbols:
            base = sym.split("_")[0].upper()
            price = self._bar_open_prices.get(base)
            if price is None or price <= 0:
                continue
            buys[sym] = price
            sells[sym] = price
            valid.append(sym)
        return buys, sells, valid

    def place_buy(
        self, symbol: str, amount_usd: float, tag: Optional[str] = None
    ) -> Optional[OrderResult]:
        base = symbol.split("_")[0].upper()
        price = self._fill_prices.get(base)
        if price is None or price <= 0:
            return OrderResult(order_id=str(uuid.uuid4()), state="failed")
        if amount_usd <= 0:
            return OrderResult(order_id=str(uuid.uuid4()), state="rejected")
        if amount_usd > self._cash + 1e-9:
            return OrderResult(order_id=str(uuid.uuid4()), state="rejected")

        qty = amount_usd / price
        self._cash -= amount_usd
        self._holdings[base] = self._holdings.get(base, 0.0) + qty

        oid = str(uuid.uuid4())
        self._orders_log.append({
            "ts": self._now_ts,
            "ts_iso": _ts_iso(self._now_ts),
            "side": "buy", "symbol": symbol,
            "qty": qty, "price": price, "notional": amount_usd,
            "tag": tag, "order_id": oid,
            "cash_after": self._cash,
        })
        return OrderResult(
            order_id=oid, state="filled",
            filled_qty=qty, avg_price=price,
            notional_usd=amount_usd, fees_usd=0.0,
        )

    def place_sell(
        self, symbol: str, qty: float
    ) -> Optional[OrderResult]:
        base = symbol.split("_")[0].upper()
        price = self._fill_prices.get(base)
        if price is None or price <= 0:
            return OrderResult(order_id=str(uuid.uuid4()), state="failed")
        held = self._holdings.get(base, 0.0)
        if qty > held + 1e-9:
            qty = held
        if qty <= 1e-12:
            return OrderResult(order_id=str(uuid.uuid4()), state="rejected")

        notional = qty * price
        self._cash += notional
        self._holdings[base] = held - qty
        if self._holdings[base] < 1e-12:
            self._holdings[base] = 0.0

        oid = str(uuid.uuid4())
        self._orders_log.append({
            "ts": self._now_ts,
            "ts_iso": _ts_iso(self._now_ts),
            "side": "sell", "symbol": symbol,
            "qty": qty, "price": price, "notional": notional,
            "tag": None, "order_id": oid,
            "cash_after": self._cash,
        })
        return OrderResult(
            order_id=oid, state="filled",
            filled_qty=qty, avg_price=price,
            notional_usd=notional, fees_usd=0.0,
        )

    def get_orders(self, symbol: str) -> dict:
        # The trader's cost-basis recompute falls back to the in-memory
        # ledger when this returns empty — exactly what we want.
        return {"results": []}

    def has_valid_trading_pairs(self) -> bool:
        return True

    def get_min_order_cost(self, symbol: str) -> float:
        return 0.0

    def to_exchange_symbol(self, canonical: str) -> str:
        return canonical

    def to_canonical_symbol(self, exchange_sym: str) -> str:
        return exchange_sym
