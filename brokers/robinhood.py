"""
Robinhood broker implementation.
Uses Ed25519 signing for API authentication.
"""

import base64
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from nacl.signing import SigningKey

from .base import BrokerAPI


class RobinhoodBroker(BrokerAPI):
    """Robinhood Crypto API implementation."""

    name = "robinhood"
    base_currency = "USD"

    def __init__(self, api_key: str, private_key_base64: str):
        """
        Initialize Robinhood broker.

        Args:
            api_key: Robinhood API key
            private_key_base64: Base64-encoded Ed25519 private key seed
        """
        self.api_key = api_key
        private_key_seed = base64.b64decode(private_key_base64)
        self.private_key = SigningKey(private_key_seed)
        self.base_url = "https://trading.robinhood.com"

        # Cache for transient API failures
        self._last_good_bid_ask: Dict[str, Dict] = {}

    def _get_current_timestamp(self) -> int:
        """Get current UTC timestamp in seconds."""
        return int(time.time())

    def _get_authorization_header(
        self, method: str, path: str, body: str, timestamp: int
    ) -> Dict[str, str]:
        """Generate authorization headers for API request."""
        message_to_sign = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self.private_key.sign(message_to_sign.encode("utf-8"))

        return {
            "x-api-key": self.api_key,
            "x-signature": base64.b64encode(signed.signature).decode("utf-8"),
            "x-timestamp": str(timestamp),
        }

    def _make_api_request(
        self, method: str, path: str, body: Optional[str] = ""
    ) -> Any:
        """Make an authenticated API request."""
        timestamp = self._get_current_timestamp()
        headers = self._get_authorization_header(method, path, body, timestamp)
        url = self.base_url + path

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=10)
            elif method == "POST":
                response = requests.post(
                    url, headers=headers, json=json.loads(body), timeout=10
                )
            else:
                return None

            response.raise_for_status()
            return response.json()
        except requests.HTTPError:
            try:
                return response.json()
            except Exception:
                return None
        except Exception:
            return None

    def get_account(self) -> Optional[Dict[str, Any]]:
        """Get account information."""
        path = "/api/v1/crypto/trading/accounts/"
        return self._make_api_request("GET", path)

    def get_holdings(self) -> Optional[Dict[str, Any]]:
        """Get current holdings."""
        path = "/api/v1/crypto/trading/holdings/"
        return self._make_api_request("GET", path)

    def get_trading_pairs(self) -> List[Dict[str, Any]]:
        """Get available trading pairs."""
        path = "/api/v1/crypto/trading/trading_pairs/"
        response = self._make_api_request("GET", path)

        if not response or "results" not in response:
            return []

        return response.get("results", [])

    def get_orders(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get order history for a symbol."""
        path = f"/api/v1/crypto/trading/orders/?symbol={symbol}"
        return self._make_api_request("GET", path)

    def get_price(
        self, symbols: List[str]
    ) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        """Get current bid/ask prices."""
        buy_prices = {}
        sell_prices = {}
        valid_symbols = []

        for symbol in symbols:
            if symbol == "USDC-USD":
                continue

            path = f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={symbol}"
            response = self._make_api_request("GET", path)

            if response and "results" in response:
                result = response["results"][0]
                ask = float(result["ask_inclusive_of_buy_spread"])
                bid = float(result["bid_inclusive_of_sell_spread"])

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
                    ask = float(cached.get("ask", 0.0) or 0.0)
                    bid = float(cached.get("bid", 0.0) or 0.0)
                    if ask > 0.0 and bid > 0.0:
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
        """Place a buy order."""
        # Get current price to calculate quantity
        buy_prices, _, _ = self.get_price([symbol])
        if symbol not in buy_prices:
            return None

        current_price = buy_prices[symbol]
        asset_quantity = amount_in_base_currency / current_price

        max_retries = 5
        response = None

        for _ in range(max_retries):
            rounded_quantity = round(asset_quantity, 8)

            body = {
                "client_order_id": client_order_id,
                "side": side,
                "type": order_type,
                "symbol": symbol,
                "market_order_config": {"asset_quantity": f"{rounded_quantity:.8f}"},
            }

            path = "/api/v1/crypto/trading/orders/"
            response = self._make_api_request("POST", path, json.dumps(body))

            if response and "errors" not in response:
                return response

            # Handle precision errors
            if response and "errors" in response:
                for error in response["errors"]:
                    detail = error.get("detail", "")
                    if "has too much precision" in detail:
                        nearest_value = detail.split("nearest ")[1].split(" ")[0]
                        decimal_places = len(nearest_value.split(".")[1].rstrip("0"))
                        asset_quantity = round(asset_quantity, decimal_places)
                        break
                    elif "must be greater than or equal to" in detail:
                        return None

        return None

    def place_sell_order(
        self,
        client_order_id: str,
        side: str,
        order_type: str,
        symbol: str,
        asset_quantity: float,
    ) -> Optional[Dict[str, Any]]:
        """Place a sell order."""
        body = {
            "client_order_id": client_order_id,
            "side": side,
            "type": order_type,
            "symbol": symbol,
            "market_order_config": {"asset_quantity": f"{asset_quantity:.8f}"},
        }

        path = "/api/v1/crypto/trading/orders/"
        response = self._make_api_request("POST", path, json.dumps(body))

        if response and isinstance(response, dict) and "errors" not in response:
            return response

        return None
