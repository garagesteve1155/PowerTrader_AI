"""
Broker module for PowerTrader AI.
Provides a unified interface for different cryptocurrency exchanges.
"""

import os
from typing import Optional

from .base import BrokerAPI
from .robinhood import RobinhoodBroker
from .bitvavo import BitvavoBroker
from .paper import PaperBroker


# Available brokers (real trading)
BROKERS = {
    "robinhood": RobinhoodBroker,
    "bitvavo": BitvavoBroker,
}


def get_broker(
    broker_name: str,
    base_dir: Optional[str] = None,
    paper_trading: bool = False,
    paper_balance: float = 10000.0,
) -> BrokerAPI:
    """
    Factory function to create a broker instance.

    Args:
        broker_name: Name of the broker ('robinhood' or 'bitvavo')
        base_dir: Base directory for credential files (default: current dir)
        paper_trading: If True, wrap broker in paper trading simulator
        paper_balance: Initial balance for paper trading

    Returns:
        Configured broker instance

    Raises:
        ValueError: If broker_name is not supported
        SystemExit: If credentials are not found
    """
    broker_name = broker_name.lower().strip()

    if broker_name not in BROKERS:
        raise ValueError(
            f"Unsupported broker: {broker_name}. "
            f"Available brokers: {', '.join(BROKERS.keys())}"
        )

    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Create the real broker
    if broker_name == "robinhood":
        real_broker = _create_robinhood_broker(base_dir)
    elif broker_name == "bitvavo":
        real_broker = _create_bitvavo_broker(base_dir)

    # Wrap in paper trading if enabled
    if paper_trading:
        state_file = os.path.join(base_dir, "paper_trading_state.json")
        return PaperBroker(
            price_source=real_broker,
            initial_balance=paper_balance,
            base_currency=real_broker.base_currency,
            state_file=state_file,
        )

    return real_broker


def _create_robinhood_broker(base_dir: str) -> RobinhoodBroker:
    """Create and configure Robinhood broker."""
    key_path = os.path.join(base_dir, "r_key.txt")
    secret_path = os.path.join(base_dir, "r_secret.txt")

    api_key = ""
    private_key = ""

    try:
        with open(key_path, "r", encoding="utf-8") as f:
            api_key = (f.read() or "").strip()
        with open(secret_path, "r", encoding="utf-8") as f:
            private_key = (f.read() or "").strip()
    except Exception:
        pass

    if not api_key or not private_key:
        print(
            "\n[PowerTrader] Robinhood API credentials not found.\n"
            "Open the GUI and go to Settings → Robinhood API → Setup / Update.\n"
            "That wizard will generate your keypair, tell you where to paste "
            "the public key on Robinhood,\n"
            "and will save r_key.txt + r_secret.txt so this trader can authenticate.\n"
        )
        raise SystemExit(1)

    return RobinhoodBroker(api_key, private_key)


def _create_bitvavo_broker(base_dir: str) -> BitvavoBroker:
    """Create and configure Bitvavo broker."""
    key_path = os.path.join(base_dir, "b_key.txt")
    secret_path = os.path.join(base_dir, "b_secret.txt")

    api_key = ""
    api_secret = ""

    try:
        with open(key_path, "r", encoding="utf-8") as f:
            api_key = (f.read() or "").strip()
        with open(secret_path, "r", encoding="utf-8") as f:
            api_secret = (f.read() or "").strip()
    except Exception:
        pass

    if not api_key or not api_secret:
        print(
            "\n[PowerTrader] Bitvavo API credentials not found.\n"
            "Open the GUI and go to Settings → Bitvavo API → Setup / Update.\n"
            "Create API keys at https://account.bitvavo.com/user/api\n"
            "and save them to b_key.txt + b_secret.txt.\n"
        )
        raise SystemExit(1)

    return BitvavoBroker(api_key, api_secret)


__all__ = [
    "BrokerAPI",
    "RobinhoodBroker",
    "BitvavoBroker",
    "PaperBroker",
    "get_broker",
    "BROKERS",
]
