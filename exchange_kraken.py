"""
Kraken exchange adapter.

Uses ccxt for all API communication.
API key and secret are read from gui_settings.json fields:
  kraken_api_key, kraken_api_secret
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

import ccxt

from exchange_api import Exchange, OrderResult
import pt_errors


class KrakenExchange(Exchange):

    @property
    def key(self) -> str:
        return "kraken"

    @property
    def display_name(self) -> str:
        return "Kraken"


    def __init__(self, api_key: str = "", api_secret: str = "",
                 debug_dir: Optional[str] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self._debug_dir = debug_dir
        self._exchange = ccxt.kraken({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        self._last_good_bid_ask: Dict[str, dict] = {}
        self._quote_map: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Symbol conversion — ccxt normalises Kraken's XBT → BTC internally
    # ------------------------------------------------------------------

    def _resolve_quote(self, base: str) -> str:
        if base in self._quote_map:
            return self._quote_map[base]
        try:
            markets = self._exchange.load_markets()
            if f"{base}/USD" in markets:
                self._quote_map[base] = "USD"
            elif f"{base}/USDT" in markets:
                self._quote_map[base] = "USDT"
            else:
                self._quote_map[base] = "USD"
        except Exception:
            return "USD"
        return self._quote_map[base]

    def to_exchange_symbol(self, canonical: str) -> str:
        base, _ = canonical.split("_")
        return f"{base}/{self._resolve_quote(base)}"

    def to_canonical_symbol(self, exchange_sym: str) -> str:
        base, quote = exchange_sym.split("/")
        return f"{base}_{quote}"

    # ------------------------------------------------------------------
    # ExchangeAdapter interface
    # ------------------------------------------------------------------

    def get_account_value(self) -> Optional[float]:
        try:
            balance = self._exchange.fetch_balance()
            free = balance.get("free", {}) or {}
            total_usd = float(free.get("USD", 0) or 0)
            total_usd += float(free.get("USDT", 0) or 0)
            totals = balance.get("total", {}) or {}
            for currency, amount in totals.items():
                amount = float(amount or 0)
                if amount <= 1e-12 or currency in ("USD", "USDT", "USDC"):
                    continue
                try:
                    quote = self._resolve_quote(currency)
                    ticker = self._exchange.fetch_ticker(f"{currency}/{quote}")
                    bid = float(ticker.get("bid", 0) or 0)
                    if bid > 0:
                        value = amount * bid
                        if value < 0.01:
                            continue
                        total_usd += value
                except Exception:
                    pass
            return total_usd
        except Exception as e:
            pt_errors.emit(
                "exchange-kraken", level="error",
                message=f"Failed to fetch account balance from Kraken: {e}",
                detail=(
                    "The total account value shown in the UI will be stale or missing. "
                    "This is typically a network or API key issue. Trading decisions that "
                    "depend on account value may be affected."
                ),
            )
            return None

    def get_buying_power(self) -> Optional[float]:
        try:
            balance = self._exchange.fetch_balance()
            free = balance.get("free", {}) or {}
            return float(free.get("USD", 0) or 0) + float(free.get("USDT", 0) or 0)
        except Exception as e:
            pt_errors.emit(
                "exchange-kraken", level="error",
                message=f"Failed to fetch buying power from Kraken: {e}",
                detail=(
                    "The trader cannot determine available USD. Buy orders may be skipped "
                    "or incorrectly sized until this resolves. Check API connectivity and key permissions."
                ),
            )
            return None

    def get_holdings(self) -> Dict[str, float]:
        try:
            balance = self._exchange.fetch_balance()
            out: Dict[str, float] = {}
            for currency, amount in (balance.get("total", {}) or {}).items():
                amount = float(amount or 0)
                if amount <= 1e-12 or currency in ("USD", "USDT"):
                    continue
                out[currency] = amount
            return out
        except Exception as e:
            pt_errors.emit(
                "exchange-kraken", level="error",
                message=f"Failed to fetch holdings from Kraken: {e}",
                detail=(
                    "Current coin positions are unknown. Sell decisions may be skipped, "
                    "and the positions panel will be empty. This typically resolves on the next polling cycle."
                ),
            )
            return {}

    def get_price(self, symbols: List[str]) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        buy_prices: Dict[str, float] = {}
        sell_prices: Dict[str, float] = {}
        valid: List[str] = []

        for canonical in symbols:
            base = self.base_from_canonical(canonical)
            if base in ("USDT", "USD"):
                continue
            exchange_sym = self.to_exchange_symbol(canonical)
            try:
                ticker = self._exchange.fetch_ticker(exchange_sym)
                ask = float(ticker.get("ask", 0) or 0)
                bid = float(ticker.get("bid", 0) or 0)
                if ask > 0 and bid > 0:
                    buy_prices[canonical] = ask
                    sell_prices[canonical] = bid
                    valid.append(canonical)
                    self._last_good_bid_ask[canonical] = {
                        "ask": ask, "bid": bid, "ts": time.time(),
                    }
                else:
                    raise ValueError("zero price")
            except Exception:
                cached = self._last_good_bid_ask.get(canonical)
                if cached:
                    ask = float(cached.get("ask", 0) or 0)
                    bid = float(cached.get("bid", 0) or 0)
                    if ask > 0 and bid > 0:
                        buy_prices[canonical] = ask
                        sell_prices[canonical] = bid
                        valid.append(canonical)

        return buy_prices, sell_prices, valid

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_buy(self, symbol: str, amount_usd: float, tag: Optional[str] = None) -> Optional[OrderResult]:
        exchange_sym = self.to_exchange_symbol(symbol)

        buy_prices, _, _ = self.get_price([symbol])
        if symbol not in buy_prices:
            return None
        current_price = buy_prices[symbol]
        qty = amount_usd / current_price

        try:
            qty = float(self._exchange.amount_to_precision(exchange_sym, qty))
        except Exception:
            qty = round(qty, 8)

        try:
            order = self._exchange.create_market_buy_order(exchange_sym, qty)
        except (ccxt.InvalidOrder, ccxt.InsufficientFunds):
            return None
        except Exception as e:
            pt_errors.emit(
                "exchange-kraken", level="error",
                message=f"Unexpected error placing buy order for {symbol}: {e}",
                detail=(
                    "The buy order was not submitted to Kraken. The trade opportunity "
                    "was missed and no funds were spent. Check Kraken API status and key permissions."
                ),
            )
            return None

        order_id = str(order.get("id", ""))
        if not order_id:
            return None

        order = self._wait_for_order_terminal(exchange_sym, order_id) or order
        return self._order_to_result(order_id, order, exchange_sym, "BUY")

    def place_sell(self, symbol: str, qty: float) -> Optional[OrderResult]:
        exchange_sym = self.to_exchange_symbol(symbol)

        try:
            qty = float(self._exchange.amount_to_precision(exchange_sym, qty))
        except Exception:
            qty = round(qty, 8)

        try:
            order = self._exchange.create_market_sell_order(exchange_sym, qty)
        except (ccxt.InvalidOrder, ccxt.InsufficientFunds):
            return None
        except Exception as e:
            pt_errors.emit(
                "exchange-kraken", level="error",
                message=f"Unexpected error placing sell order for {symbol}: {e}",
                detail=(
                    "The sell order was not submitted to Kraken. The position remains open "
                    "and the trader may attempt to sell again on the next cycle. "
                    "Check Kraken API status and key permissions."
                ),
            )
            return None

        order_id = str(order.get("id", ""))
        if not order_id:
            return None

        order = self._wait_for_order_terminal(exchange_sym, order_id) or order
        return self._order_to_result(order_id, order, exchange_sym, "SELL")

    def get_orders(self, symbol: str) -> dict:
        exchange_sym = self.to_exchange_symbol(symbol)
        try:
            closed = self._exchange.fetch_closed_orders(exchange_sym)
            open_orders = self._exchange.fetch_open_orders(exchange_sym)
            return {"results": [self._normalize_order(o) for o in closed + open_orders]}
        except Exception as e:
            pt_errors.emit(
                "exchange-kraken", level="warning",
                message=f"Failed to fetch orders for {symbol} from Kraken: {e}",
                detail=(
                    "Order history for this coin is unavailable. Cost basis calculations "
                    "may be incomplete until the next successful fetch."
                ),
            )
            return {"results": []}

    @staticmethod
    def _normalize_order(o: dict) -> dict:
        """Map ccxt order fields to the shape pt_trader expects."""
        status = str(o.get("status", "")).lower()
        state_map = {"closed": "filled", "canceled": "canceled",
                     "cancelled": "canceled", "expired": "canceled"}
        o["state"] = state_map.get(status, status)
        o["created_at"] = o.get("datetime") or ""
        avg = o.get("average") or o.get("price")
        if avg is not None:
            o["average_price"] = avg
        filled = o.get("filled")
        if filled is not None:
            o["filled_asset_quantity"] = filled
        return o

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    def get_order_result(self, symbol: str, order_id: str) -> Optional[OrderResult]:
        exchange_sym = self.to_exchange_symbol(symbol)
        try:
            order = self._exchange.fetch_order(order_id, exchange_sym)
        except Exception:
            return None
        if not order:
            return None
        return self._order_to_result(order_id, order, exchange_sym, None)

    def has_valid_trading_pairs(self) -> bool:
        try:
            markets = self._exchange.load_markets()
            return bool(markets)
        except Exception:
            return False

    def get_min_order_cost(self, symbol: str) -> float:
        try:
            exchange_sym = self.to_exchange_symbol(symbol)
            markets = self._exchange.load_markets()
            m = markets.get(exchange_sym, {})
            limits = m.get("limits", {})
            min_cost = float(limits.get("cost", {}).get("min", 0) or 0)
            min_amt = float(limits.get("amount", {}).get("min", 0) or 0)
            if min_amt > 0 and min_cost > 0:
                ticker = self._exchange.fetch_ticker(exchange_sym)
                price = float(ticker.get("ask", 0) or 0)
                if price > 0:
                    return max(min_cost, min_amt * price)
            return min_cost
        except Exception:
            return 0.0

    def calculate_cost_basis_from_orders(
        self,
        bot_order_ids: Dict[str, set],
        bot_order_ids_from_history: Dict[str, set],
        pnl_ledger: dict,
    ) -> Dict[str, float]:
        holdings = self.get_holdings()
        if not holdings:
            return {}

        cost_basis: Dict[str, float] = {}

        for asset_code in holdings:
            try:
                pos = (pnl_ledger.get("open_positions", {}) or {}).get(asset_code)
                tradable_target_qty = (
                    float(pos.get("qty", 0) or 0) if isinstance(pos, dict) else 0.0
                )
            except Exception:
                tradable_target_qty = 0.0

            if tradable_target_qty <= 1e-12:
                cost_basis[asset_code] = 0.0
                continue

            orders_resp = self.get_orders(f"{asset_code}_USD")
            orders_list = orders_resp.get("results", []) or []

            all_ids: set = set()
            for src in (bot_order_ids, bot_order_ids_from_history):
                if isinstance(src, dict):
                    ids = src.get(asset_code, set())
                    if isinstance(ids, (set, frozenset, list)):
                        all_ids.update(ids)

            filled_bot_buys = []
            for o in orders_list:
                try:
                    if o.get("state") != "filled":
                        continue
                    if str(o.get("side", "")).lower() != "buy":
                        continue
                    oid = str(o.get("id", "")).strip()
                    if oid and oid in all_ids:
                        filled_bot_buys.append(o)
                except Exception:
                    continue

            if not filled_bot_buys:
                continue

            filled_bot_buys.sort(key=lambda x: x.get("created_at") or "")

            lots = []
            for o in filled_bot_buys:
                try:
                    q = float(o.get("filled", 0) or 0)
                    p = (
                        float(o.get("average", 0) or 0)
                        if o.get("average")
                        else float(o.get("price", 0) or 0)
                    )
                    if q > 0 and p > 0:
                        lots.append((q, p))
                except Exception:
                    continue

            bot_qty = sum(q for q, _ in lots)
            if bot_qty <= 1e-12:
                cost_basis[asset_code] = 0.0
                continue

            target_qty = min(bot_qty, tradable_target_qty)
            remaining = target_qty
            total_cost = 0.0
            for q, p in lots:
                if remaining <= 0:
                    break
                use_q = min(q, remaining)
                total_cost += use_q * p
                remaining -= use_q

            cost_basis[asset_code] = (
                total_cost / target_qty if target_qty > 1e-12 else 0.0
            )

        return cost_basis

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _order_to_result(
        self, order_id: str, order: dict, exchange_sym: str, side: Optional[str],
    ) -> Optional[OrderResult]:
        status = str(order.get("status", "")).lower()
        state_map = {
            "closed": "filled",
            "canceled": "canceled",
            "cancelled": "canceled",
            "expired": "canceled",
            "rejected": "rejected",
        }
        state = state_map.get(status, status)

        if state != "filled":
            return OrderResult(order_id=order_id, state=state, raw=order)

        filled_qty = float(order.get("filled", 0) or 0)
        avg_price = (
            float(order.get("average", 0) or 0) if order.get("average") else None
        )
        cost = float(order.get("cost", 0) or 0) if order.get("cost") else None
        fee_cost = None
        fee = order.get("fee")
        if isinstance(fee, dict) and fee.get("cost") is not None:
            fee_cost = float(fee["cost"])

        if side:
            self._write_debug_dump(exchange_sym, side, order_id, order)

        return OrderResult(
            order_id=order_id,
            state="filled",
            filled_qty=filled_qty,
            avg_price=avg_price,
            notional_usd=cost,
            fees_usd=fee_cost,
            raw=order,
        )

    def _wait_for_order_terminal(
        self, exchange_sym: str, order_id: str, timeout: float = 60.0,
    ) -> Optional[dict]:
        terminal = {"closed", "canceled", "cancelled", "expired", "rejected"}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                order = self._exchange.fetch_order(order_id, exchange_sym)
                if str(order.get("status", "")).lower() in terminal:
                    return order
            except Exception:
                pass
            time.sleep(1)
        return None

    def _write_debug_dump(
        self, exchange_sym: str, side: str, order_id: str, order: dict,
    ) -> None:
        if not self._debug_dir:
            return
        try:
            import datetime

            os.makedirs(self._debug_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_sym = exchange_sym.replace("/", "_")
            path = os.path.join(
                self._debug_dir, f"{ts}_{safe_sym}_{side}_{order_id}.txt",
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(order, indent=2, default=str))
        except Exception:
            pass


def create_exchange() -> KrakenExchange:
    import os
    from exchange_api import load_api_keys
    from pt_env import PTEnv

    keys = load_api_keys("kraken")
    env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
    debug_dir = str(env.debug_trade_dumps_dir())

    return KrakenExchange(api_key=keys["api_key"], api_secret=keys["api_secret"], debug_dir=debug_dir)
