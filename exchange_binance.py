"""
Binance exchange adapter (stub).

To implement: install ccxt (`pip install ccxt`) and fill in the methods below.
API key and secret are read from gui_settings.json fields:
  binance_api_key, binance_api_secret
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from exchange_api import Exchange, OrderResult


class BinanceExchange(Exchange):

    @property
    def key(self) -> str:
        return "binance"

    @property
    def display_name(self) -> str:
        return "Binance"


    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret

    def to_exchange_symbol(self, canonical: str) -> str:
        # Binance uses BTCUSDT format
        base, quote = canonical.split("_")
        return f"{base}{quote}T" if quote == "USD" else f"{base}{quote}"

    def to_canonical_symbol(self, exchange_sym: str) -> str:
        if exchange_sym.endswith("USDT"):
            return exchange_sym[:-4] + "_USD"
        return exchange_sym

    def get_account_value(self) -> Optional[float]:
        raise NotImplementedError("Binance adapter not yet implemented")

    def get_buying_power(self) -> Optional[float]:
        raise NotImplementedError("Binance adapter not yet implemented")

    def get_holdings(self) -> Dict[str, float]:
        raise NotImplementedError("Binance adapter not yet implemented")

    def get_price(self, symbols: List[str]) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        raise NotImplementedError("Binance adapter not yet implemented")

    def place_buy(self, symbol: str, amount_usd: float, tag: Optional[str] = None) -> Optional[OrderResult]:
        raise NotImplementedError("Binance adapter not yet implemented")

    def place_sell(self, symbol: str, qty: float) -> Optional[OrderResult]:
        raise NotImplementedError("Binance adapter not yet implemented")

    def get_orders(self, symbol: str) -> dict:
        raise NotImplementedError("Binance adapter not yet implemented")


def create_exchange() -> BinanceExchange:
    from exchange_api import load_api_keys
    keys = load_api_keys("binance")
    return BinanceExchange(api_key=keys["api_key"], api_secret=keys["api_secret"])
