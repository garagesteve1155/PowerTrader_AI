"""
Abstract base class for broker implementations.
All broker integrations (Robinhood, Bitvavo, etc.) should inherit from this class.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class BrokerAPI(ABC):
    """Abstract base class defining the interface for all broker implementations."""

    # Broker identification
    name: str = "base"
    base_currency: str = "USD"

    @abstractmethod
    def get_account(self) -> Optional[Dict[str, Any]]:
        """
        Get account information including buying power.

        Returns:
            Dict with account info or None on failure.
            Expected keys: 'buying_power', 'buying_power_currency'
        """
        pass

    @abstractmethod
    def get_holdings(self) -> Optional[Dict[str, Any]]:
        """
        Get current holdings/positions.

        Returns:
            Dict with holdings info or None on failure.
            Expected format: {'results': [{'asset_code': 'BTC', 'total_quantity': '0.5'}, ...]}
        """
        pass

    @abstractmethod
    def get_trading_pairs(self) -> List[Dict[str, Any]]:
        """
        Get available trading pairs.

        Returns:
            List of trading pair dicts or empty list on failure.
        """
        pass

    @abstractmethod
    def get_orders(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get order history for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC-USD' or 'BTC-EUR')

        Returns:
            Dict with orders info or None on failure.
            Expected format: {'results': [order1, order2, ...]}
        """
        pass

    @abstractmethod
    def get_price(self, symbols: List[str]) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        """
        Get current bid/ask prices for symbols.

        Args:
            symbols: List of trading pair symbols

        Returns:
            Tuple of (buy_prices, sell_prices, valid_symbols)
            - buy_prices: {symbol: ask_price}
            - sell_prices: {symbol: bid_price}
            - valid_symbols: list of symbols that returned valid prices
        """
        pass

    @abstractmethod
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

        Args:
            client_order_id: Unique order identifier
            side: 'buy'
            order_type: 'market' or 'limit'
            symbol: Trading pair symbol
            amount_in_base_currency: Amount to spend in base currency (USD/EUR)

        Returns:
            Order response dict or None on failure.
        """
        pass

    @abstractmethod
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

        Args:
            client_order_id: Unique order identifier
            side: 'sell'
            order_type: 'market' or 'limit'
            symbol: Trading pair symbol
            asset_quantity: Amount of asset to sell

        Returns:
            Order response dict or None on failure.
        """
        pass

    def format_symbol(self, coin: str) -> str:
        """
        Format a coin symbol to the broker's trading pair format.

        Args:
            coin: Base coin symbol (e.g., 'BTC')

        Returns:
            Formatted trading pair (e.g., 'BTC-USD' or 'BTC-EUR')
        """
        return f"{coin}-{self.base_currency}"

    def extract_coin(self, symbol: str) -> str:
        """
        Extract the coin symbol from a trading pair.

        Args:
            symbol: Trading pair (e.g., 'BTC-USD')

        Returns:
            Coin symbol (e.g., 'BTC')
        """
        return symbol.split("-")[0]
