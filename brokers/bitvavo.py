"""
Bitvavo broker implementation.
Uses the official python-bitvavo-api SDK with HMAC-SHA256 authentication.
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    from python_bitvavo_api.bitvavo import Bitvavo
except ImportError:
    raise ImportError(
        "python-bitvavo-api is not installed. "
        "Install it with: pip install python-bitvavo-api"
    )

from .base import BrokerAPI


class BitvavoBroker(BrokerAPI):
    """Bitvavo API implementation using official SDK."""

    name = "bitvavo"
    base_currency = "EUR"

    def __init__(self, api_key: str, api_secret: str):
        """
        Initialize Bitvavo broker.

        Args:
            api_key: Bitvavo API key
            api_secret: Bitvavo API secret
        """
        self.client = Bitvavo({
            "APIKEY": api_key,
            "APISECRET": api_secret,
            "RESTURL": "https://api.bitvavo.com/v2",
            "WSURL": "wss://ws.bitvavo.com/v2/",
            "ACCESSWINDOW": 10000,
        })

        # Cache for transient API failures
        self._last_good_bid_ask: Dict[str, Dict] = {}

    def get_account(self) -> Optional[Dict[str, Any]]:
        """
        Get account information.

        Returns Bitvavo balance formatted to match expected structure.
        """
        try:
            balance = self.client.balance({})

            if isinstance(balance, dict) and "error" in balance:
                return None

            # Calculate total EUR balance (available + in order)
            eur_balance = 0.0
            for item in balance:
                if item.get("symbol") == "EUR":
                    eur_balance = float(item.get("available", 0))
                    break

            return {
                "buying_power": eur_balance,
                "buying_power_currency": "EUR",
            }
        except Exception:
            return None

    def get_holdings(self) -> Optional[Dict[str, Any]]:
        """
        Get current holdings.

        Returns balances formatted to match expected structure.
        """
        try:
            balance = self.client.balance({})

            if isinstance(balance, dict) and "error" in balance:
                return None

            results = []
            for item in balance:
                symbol = item.get("symbol", "")
                available = float(item.get("available", 0))
                in_order = float(item.get("inOrder", 0))
                total = available + in_order

                # Skip EUR and zero balances
                if symbol == "EUR" or total <= 0:
                    continue

                results.append({
                    "asset_code": symbol,
                    "total_quantity": str(total),
                    "available_quantity": str(available),
                    "in_order_quantity": str(in_order),
                })

            return {"results": results}
        except Exception:
            return None

    def get_trading_pairs(self) -> List[Dict[str, Any]]:
        """Get available trading pairs."""
        try:
            markets = self.client.markets({})

            if isinstance(markets, dict) and "error" in markets:
                return []

            # Filter for EUR pairs only
            eur_pairs = [
                market for market in markets
                if market.get("quote") == "EUR" and market.get("status") == "trading"
            ]

            return eur_pairs
        except Exception:
            return []

    def get_orders(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get order history for a symbol.

        Args:
            symbol: Trading pair (e.g., 'BTC-EUR')
        """
        try:
            # Convert format if needed (BTC-EUR -> BTC-EUR)
            market = symbol.replace("-", "-")

            # Get trades (filled orders)
            trades = self.client.trades(market, {})

            if isinstance(trades, dict) and "error" in trades:
                return None

            # Format to match expected structure
            results = []
            for trade in trades:
                results.append({
                    "id": trade.get("id"),
                    "side": trade.get("side"),
                    "state": "filled",
                    "created_at": trade.get("timestamp"),
                    "executions": [{
                        "quantity": trade.get("amount"),
                        "effective_price": trade.get("price"),
                    }],
                })

            return {"results": results}
        except Exception:
            return None

    def get_price(
        self, symbols: List[str]
    ) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        """Get current bid/ask prices."""
        buy_prices = {}
        sell_prices = {}
        valid_symbols = []

        for symbol in symbols:
            try:
                # Get ticker for this market
                ticker = self.client.tickerBook({"market": symbol})

                if isinstance(ticker, dict) and "error" not in ticker:
                    ask = float(ticker.get("ask", 0))
                    bid = float(ticker.get("bid", 0))

                    if ask > 0 and bid > 0:
                        buy_prices[symbol] = ask
                        sell_prices[symbol] = bid
                        valid_symbols.append(symbol)

                        # Update cache
                        self._last_good_bid_ask[symbol] = {
                            "ask": ask,
                            "bid": bid,
                            "ts": time.time(),
                        }
                else:
                    # Fallback to cached prices
                    cached = self._last_good_bid_ask.get(symbol)
                    if cached:
                        ask = float(cached.get("ask", 0) or 0)
                        bid = float(cached.get("bid", 0) or 0)
                        if ask > 0 and bid > 0:
                            buy_prices[symbol] = ask
                            sell_prices[symbol] = bid
                            valid_symbols.append(symbol)
            except Exception:
                # Fallback to cached prices
                cached = self._last_good_bid_ask.get(symbol)
                if cached:
                    ask = float(cached.get("ask", 0) or 0)
                    bid = float(cached.get("bid", 0) or 0)
                    if ask > 0 and bid > 0:
                        buy_prices[symbol] = ask
                        sell_prices[symbol] = bid
                        valid_symbols.append(symbol)

        return buy_prices, sell_prices, valid_symbols

    def place_buy_order(
        self,
        client_order_id: str,
        side: str,
        order_type: str,
        symbol: str,
        amount_in_base_currency: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Place a buy order.

        For market orders, Bitvavo accepts 'amountQuote' (EUR amount to spend).
        """
        try:
            order_params = {
                "market": symbol,
                "side": "buy",
                "orderType": "market",
                "amountQuote": str(round(amount_in_base_currency, 2)),
            }

            response = self.client.placeOrder(
                symbol,
                "buy",
                "market",
                order_params
            )

            if isinstance(response, dict) and "error" in response:
                return None

            return response
        except Exception:
            return None

    def place_sell_order(
        self,
        client_order_id: str,
        side: str,
        order_type: str,
        symbol: str,
        asset_quantity: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Place a sell order.

        For market orders, Bitvavo accepts 'amount' (asset quantity to sell).
        """
        try:
            order_params = {
                "market": symbol,
                "side": "sell",
                "orderType": "market",
                "amount": str(asset_quantity),
            }

            response = self.client.placeOrder(
                symbol,
                "sell",
                "market",
                order_params
            )

            if isinstance(response, dict) and "error" in response:
                return None

            return response
        except Exception:
            return None
