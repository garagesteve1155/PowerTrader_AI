"""
Paper trading broker implementation.
Simulates trades without using real money - perfect for testing strategies.
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .base import BrokerAPI


class PaperBroker(BrokerAPI):
    """
    Paper trading broker that simulates trades with virtual money.

    Uses real market data from another broker but executes virtual trades.
    State is persisted to disk so it survives restarts.
    """

    name = "paper"

    def __init__(
        self,
        price_source: BrokerAPI,
        initial_balance: float = 10000.0,
        base_currency: str = "EUR",
        state_file: str = "paper_trading_state.json",
    ):
        """
        Initialize paper trading broker.

        Args:
            price_source: Real broker to get market prices from
            initial_balance: Starting virtual balance
            base_currency: Base currency (EUR or USD)
            state_file: File to persist state
        """
        self.price_source = price_source
        self.base_currency = base_currency
        self._state_file = state_file
        self._initial_balance = initial_balance

        # Load or initialize state
        self._state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """Load state from disk or initialize fresh state."""
        if os.path.isfile(self._state_file):
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    # Validate state has required keys
                    if all(k in state for k in ["balance", "holdings", "orders", "trades"]):
                        return state
            except Exception:
                pass

        # Initialize fresh state
        return {
            "balance": self._initial_balance,
            "holdings": {},  # {"BTC": {"quantity": 0.5, "avg_cost": 45000.0}, ...}
            "orders": [],    # Order history
            "trades": [],    # Trade history
            "created_at": time.time(),
        }

    def _save_state(self) -> None:
        """Persist state to disk."""
        try:
            self._state["updated_at"] = time.time()
            tmp = f"{self._state_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp, self._state_file)
        except Exception:
            pass

    def reset(self, new_balance: Optional[float] = None) -> None:
        """Reset paper trading state to initial values."""
        self._state = {
            "balance": new_balance or self._initial_balance,
            "holdings": {},
            "orders": [],
            "trades": [],
            "created_at": time.time(),
        }
        self._save_state()

    def get_account(self) -> Optional[Dict[str, Any]]:
        """Get virtual account information."""
        return {
            "buying_power": self._state["balance"],
            "buying_power_currency": self.base_currency,
            "paper_trading": True,
        }

    def get_holdings(self) -> Optional[Dict[str, Any]]:
        """Get virtual holdings."""
        results = []
        for asset, data in self._state["holdings"].items():
            qty = data.get("quantity", 0)
            if qty > 0:
                results.append({
                    "asset_code": asset,
                    "total_quantity": str(qty),
                    "available_quantity": str(qty),
                    "avg_cost": data.get("avg_cost", 0),
                })
        return {"results": results}

    def get_trading_pairs(self) -> List[Dict[str, Any]]:
        """Get trading pairs from price source."""
        return self.price_source.get_trading_pairs()

    def get_orders(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get virtual order history for a symbol."""
        coin = self.extract_coin(symbol)
        orders = [o for o in self._state["orders"] if o.get("coin") == coin]

        # Format to match expected structure
        results = []
        for o in orders:
            results.append({
                "id": o.get("id"),
                "side": o.get("side"),
                "state": "filled",
                "created_at": o.get("timestamp"),
                "executions": [{
                    "quantity": o.get("quantity"),
                    "effective_price": o.get("price"),
                }],
            })

        return {"results": results}

    def get_price(
        self, symbols: List[str]
    ) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        """Get real market prices from price source."""
        return self.price_source.get_price(symbols)

    def place_buy_order(
        self,
        client_order_id: str,
        side: str,
        order_type: str,
        symbol: str,
        amount_in_base_currency: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate a buy order.

        Deducts from virtual balance and adds to holdings.
        """
        # Get current price
        buy_prices, _, valid = self.get_price([symbol])
        if symbol not in buy_prices:
            return None

        price = buy_prices[symbol]

        # Check sufficient balance
        if amount_in_base_currency > self._state["balance"]:
            return None

        # Calculate quantity
        quantity = amount_in_base_currency / price
        coin = self.extract_coin(symbol)

        # Update balance
        self._state["balance"] -= amount_in_base_currency

        # Update holdings with weighted average cost
        if coin in self._state["holdings"]:
            existing = self._state["holdings"][coin]
            old_qty = existing.get("quantity", 0)
            old_cost = existing.get("avg_cost", 0)
            new_qty = old_qty + quantity
            # Weighted average cost
            if new_qty > 0:
                new_avg_cost = ((old_qty * old_cost) + (quantity * price)) / new_qty
            else:
                new_avg_cost = price
            self._state["holdings"][coin] = {
                "quantity": new_qty,
                "avg_cost": new_avg_cost,
            }
        else:
            self._state["holdings"][coin] = {
                "quantity": quantity,
                "avg_cost": price,
            }

        # Record order
        order_id = str(uuid.uuid4())
        order = {
            "id": order_id,
            "client_order_id": client_order_id,
            "coin": coin,
            "symbol": symbol,
            "side": "buy",
            "quantity": quantity,
            "price": price,
            "amount": amount_in_base_currency,
            "timestamp": time.time(),
        }
        self._state["orders"].append(order)
        self._state["trades"].append(order)

        self._save_state()

        return {
            "id": order_id,
            "state": "filled",
            "side": "buy",
            "quantity": quantity,
            "price": price,
            "paper_trading": True,
        }

    def place_sell_order(
        self,
        client_order_id: str,
        side: str,
        order_type: str,
        symbol: str,
        asset_quantity: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate a sell order.

        Removes from holdings and adds to virtual balance.
        """
        coin = self.extract_coin(symbol)

        # Check sufficient holdings
        holding = self._state["holdings"].get(coin, {})
        available = holding.get("quantity", 0)

        if asset_quantity > available:
            return None

        # Get current price
        _, sell_prices, valid = self.get_price([symbol])
        if symbol not in sell_prices:
            return None

        price = sell_prices[symbol]
        amount = asset_quantity * price

        # Update holdings
        new_qty = available - asset_quantity
        if new_qty <= 0.00000001:  # Effectively zero
            del self._state["holdings"][coin]
        else:
            self._state["holdings"][coin]["quantity"] = new_qty

        # Update balance
        self._state["balance"] += amount

        # Record order
        order_id = str(uuid.uuid4())
        order = {
            "id": order_id,
            "client_order_id": client_order_id,
            "coin": coin,
            "symbol": symbol,
            "side": "sell",
            "quantity": asset_quantity,
            "price": price,
            "amount": amount,
            "timestamp": time.time(),
            "avg_cost": holding.get("avg_cost", 0),
        }
        self._state["orders"].append(order)
        self._state["trades"].append(order)

        self._save_state()

        return {
            "id": order_id,
            "state": "filled",
            "side": "sell",
            "quantity": asset_quantity,
            "price": price,
            "paper_trading": True,
        }

    def get_performance(self) -> Dict[str, Any]:
        """
        Calculate paper trading performance metrics.

        Returns:
            Dict with performance stats
        """
        # Get current prices for holdings
        holdings = self._state["holdings"]
        symbols = [self.format_symbol(coin) for coin in holdings.keys()]

        holdings_value = 0.0
        if symbols:
            _, sell_prices, _ = self.get_price(symbols)
            for coin, data in holdings.items():
                symbol = self.format_symbol(coin)
                price = sell_prices.get(symbol, 0)
                qty = data.get("quantity", 0)
                holdings_value += qty * price

        total_value = self._state["balance"] + holdings_value
        profit_loss = total_value - self._initial_balance
        profit_pct = (profit_loss / self._initial_balance) * 100 if self._initial_balance > 0 else 0

        # Count trades
        buy_count = sum(1 for t in self._state["trades"] if t["side"] == "buy")
        sell_count = sum(1 for t in self._state["trades"] if t["side"] == "sell")

        return {
            "initial_balance": self._initial_balance,
            "current_balance": self._state["balance"],
            "holdings_value": holdings_value,
            "total_value": total_value,
            "profit_loss": profit_loss,
            "profit_pct": profit_pct,
            "total_trades": len(self._state["trades"]),
            "buy_trades": buy_count,
            "sell_trades": sell_count,
            "base_currency": self.base_currency,
        }
