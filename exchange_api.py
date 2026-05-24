"""
Abstract base class for exchanges.

Each exchange lives in exchange_<exchange>.py and subclasses Exchange.
The exchange key is the lowercase slug derived from the filename.
"""

from __future__ import annotations

import glob
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class OrderResult:
    order_id: str
    state: str  # "filled", "canceled", "rejected", "failed"
    filled_qty: float = 0.0
    avg_price: Optional[float] = None
    notional_usd: Optional[float] = None
    fees_usd: Optional[float] = None
    raw: dict = field(default_factory=dict)


@dataclass
class PriceQuote:
    symbol: str  # canonical BTC_USD
    bid: float
    ask: float


class Exchange(ABC):
    """Thin exchange interface: HTTP/auth/data-format only. No accounting, no strategy."""

    # ------------------------------------------------------------------
    # Self-description (abstract — each exchange must declare these)
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def key(self) -> str:
        """Lowercase slug identifying this exchange/account (e.g. 'kraken', 'demo', 'shadow')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in the UI (e.g. 'Kraken', 'Demo', 'Shadow')."""

    @property
    def is_paper(self) -> bool:
        """True for frictionless simulated accounts (PaperExchange)."""
        return False

    def all_accounts(self) -> List['Exchange']:
        """Return all accounts this exchange exposes (default: just self).

        ShadowedExchange overrides this to return [self, self._shadow].
        """
        return [self]

    def tick(self) -> None:
        """Called at the end of each pt_trader manage_trades() loop.

        ShadowedExchange overrides this to run periodic shadow maintenance
        (append account value history, write shadow trader_status.json).
        """

    # ------------------------------------------------------------------
    # Core trading interface (abstract)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_account_value(self) -> Optional[float]:
        """Return total account value in USD, or None if unavailable."""

    @abstractmethod
    def get_buying_power(self) -> Optional[float]:
        """Return available buying power in USD."""

    @abstractmethod
    def get_holdings(self) -> Dict[str, float]:
        """Return {base_symbol: quantity} for all held assets (e.g. {"BTC": 0.5})."""

    @abstractmethod
    def get_price(self, symbols: List[str]) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        """
        Given canonical symbols like ["BTC_USD", "ETH_USD"], return:
          (buy_prices, sell_prices, valid_symbols)
        where buy_prices/sell_prices map canonical symbol -> float.
        """

    @abstractmethod
    def place_buy(self, symbol: str, amount_usd: float, tag: Optional[str] = None) -> Optional[OrderResult]:
        """
        Place a market buy for ~amount_usd worth of symbol (canonical format).
        Block until terminal. Return OrderResult or None on total failure.
        """

    @abstractmethod
    def place_sell(self, symbol: str, qty: float) -> Optional[OrderResult]:
        """
        Place a market sell of qty units of symbol (canonical format).
        Block until terminal. Return OrderResult or None on total failure.
        """

    @abstractmethod
    def get_orders(self, symbol: str) -> dict:
        """Return order history for symbol. Shape is exchange-specific (used for cost basis replay)."""

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    def get_order_result(self, symbol: str, order_id: str) -> Optional[OrderResult]:
        """Look up an existing order by ID (for crash recovery). Override if supported."""
        return None

    def has_valid_trading_pairs(self) -> bool:
        """Return False if the exchange can't trade right now. Default: always True."""
        return True

    def calculate_cost_basis_from_orders(
        self,
        bot_order_ids: Dict[str, set],
        bot_order_ids_from_history: Dict[str, set],
        pnl_ledger: dict,
    ) -> Dict[str, float]:
        """
        Optional: compute cost basis from exchange order history.
        Return empty dict to signal 'use ledger instead'.
        """
        return {}

    def get_filled_bot_buy_qty(self, base_symbol: str, bot_order_ids: Dict[str, set]) -> float:
        """Return net filled qty for bot-owned orders. Used for ledger seeding. 0 = use ledger."""
        return 0.0

    def get_min_order_cost(self, symbol: str) -> float:
        """Return minimum order cost in USD for symbol. 0 = no minimum."""
        return 0.0

    # ------------------------------------------------------------------
    # Symbol conversion helpers
    # ------------------------------------------------------------------

    def to_exchange_symbol(self, canonical: str) -> str:
        """Convert canonical 'BTC_USD' to exchange-native format. Override if needed."""
        return canonical.replace("_", "-")

    def to_canonical_symbol(self, exchange_sym: str) -> str:
        """Convert exchange-native symbol back to canonical 'BTC_USD'. Override if needed."""
        return exchange_sym.replace("-", "_")

    def base_from_canonical(self, canonical: str) -> str:
        return canonical.split("_")[0].upper()


# ------------------------------------------------------------------
# Auto-discovery: scan for exchange_*.py to find available exchanges
# ------------------------------------------------------------------

_SYNTHETIC_EXCHANGES = {"api", "paper"}


def discover_exchanges(search_dir: Optional[str] = None) -> List[str]:
    """Return sorted list of real exchange keys found as exchange_<key>.py files.

    Excludes synthetic modules (api, paper) — those are managed internally.
    """
    if search_dir is None:
        search_dir = os.path.dirname(os.path.abspath(__file__))
    keys = []
    for path in glob.glob(os.path.join(search_dir, "exchange_*.py")):
        name = os.path.basename(path)
        key = name[len("exchange_"):-len(".py")]
        if key in _SYNTHETIC_EXCHANGES:
            continue
        keys.append(key)
    return sorted(keys)


def load_api_keys(exchange_name: str) -> dict:
    """Load credentials for *exchange_name* from exchange_api_keys.json."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    keys_path = os.path.join(base_dir, "exchange_api_keys.json")
    if not os.path.isfile(keys_path):
        raise FileNotFoundError(
            f"exchange_api_keys.json not found. Copy exchange_api_keys.json.template "
            f"to exchange_api_keys.json and fill in your credentials."
        )
    with open(keys_path, "r", encoding="utf-8") as f:
        import json as _json
        all_keys = _json.load(f)
    entry = all_keys.get(exchange_name) or {}
    return {
        "api_key": str(entry.get("api_key") or "").strip(),
        "api_secret": str(entry.get("api_secret") or "").strip(),
    }


def exchange_display_name(key: str) -> str:
    """'robinhood' -> 'Robinhood', 'demo' -> 'Demo'"""
    return key.replace("_", " ").title()


def load_exchange(key: str) -> Exchange:
    """Create and return an Exchange for the given key.

    'demo'           → PaperExchange(key='demo')
    '<real_exchange>' → ShadowedExchange(RealExchange, PaperExchange(key='shadow'))
    """
    from pt_env import PTEnv
    env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
    cfg = env.get_config()

    if key == "demo":
        from exchange_paper import PaperExchange
        return PaperExchange(
            key="demo",
            starting_usd=float(cfg.get("demo_starting_usd") or 0),
            price_source=cfg.get("live_price_source", "kucoin"),
            state_path=str(env.exchange_state_path("demo")),
        )

    import importlib
    mod = importlib.import_module(f"exchange_{key}")
    real = mod.create_exchange()

    from exchange_paper import PaperExchange, ShadowedExchange
    shadow_usd = float(
        cfg.get("shadow_starting_usd") or cfg.get("control_starting_usd") or 0
    )
    shadow = PaperExchange(
        key="shadow",
        starting_usd=shadow_usd,
        price_source=cfg.get("live_price_source", "kucoin"),
        state_path=str(env.exchange_state_path("shadow")),
    )
    return ShadowedExchange(real, shadow)
