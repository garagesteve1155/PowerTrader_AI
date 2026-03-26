"""
Exchange API Abstraction Layer
Allows switching between KuCoin Simulator and real KuCoin API
without changing trading logic.
"""

import os
from typing import Dict, Optional, List
from pt_kucoin_simulator import KuCoinSimulator
from pt_kucoin_api import KuCoinAPI, load_kucoin_credentials


# Global exchange instance (will be initialized once)
_exchange = None
_exchange_mode = "SIMULATOR"  # Can be "SIMULATOR" or "KUCOIN_REAL"


def _is_success_status(status: object) -> bool:
    return str(status or "").strip().upper() in {"DONE", "SUCCESS", "FILLED", "OK"}


def initialize_exchange(mode: str = "SIMULATOR", initial_usdt: float = 50.0, simulator_log: str = None):
    """
    Initialize the exchange API.
    
    Args:
        mode: "SIMULATOR" or "KUCOIN_REAL"
        initial_usdt: Starting balance for simulator
    """
    global _exchange, _exchange_mode
    
    mode = mode.upper().strip()
    
    if mode == "SIMULATOR":
        _exchange = KuCoinSimulator(initial_usdt=initial_usdt, simulator_log=simulator_log)
        _exchange_mode = "SIMULATOR"
        print(f"✅ Exchange initialized in SIMULATOR mode (${initial_usdt:.2f} USDT)")
    
    elif mode == "KUCOIN_REAL":
        api_key, api_secret, api_passphrase = load_kucoin_credentials()
        if not api_key:
            print("❌ KuCoin credentials not found. Falling back to SIMULATOR.")
            _exchange = KuCoinSimulator(initial_usdt=initial_usdt, simulator_log=simulator_log)
            _exchange_mode = "SIMULATOR"
        else:
            _exchange = KuCoinAPI(api_key, api_secret, api_passphrase)
            _exchange_mode = "KUCOIN_REAL"
            print("✅ Exchange initialized in KUCOIN_REAL mode (LIVE TRADING)")
    
    else:
        raise ValueError(f"Unknown exchange mode: {mode}")


def get_exchange_instance() -> KuCoinSimulator:
    """Get the current exchange instance."""
    if _exchange is None:
        initialize_exchange()
    return _exchange


def get_exchange_mode() -> str:
    """Get current exchange mode."""
    return _exchange_mode


# ============================================
# High-Level Trading API
# ============================================

def buy(symbol: str, quantity: float) -> Dict:
    """
    Place a market BUY order.
    
    Args:
        symbol: e.g., "BTC-USDT" or "BTC"
        quantity: Amount to buy
    
    Returns:
        {
            "success": bool,
            "orderId": str,
            "symbol": str,
            "side": "buy",
            "quantity": float,
            "price": float,
            "total": float,
            "message": str
        }
    """
    exchange = get_exchange_instance()
    
    # Normalize symbol
    if not symbol.endswith("-USDT"):
        symbol = f"{symbol}-USDT"
    
    result = exchange.place_market_order(symbol, "buy", quantity)
    success = _is_success_status(result.get("status")) or _is_success_status(result.get("success"))
    
    return {
        "success": success,
        "orderId": result.get("orderId"),
        "symbol": symbol,
        "side": "buy",
        "quantity": quantity,
        "price": result.get("price", 0),
        "total": result.get("total", 0),
        "message": result.get("message", "Unknown error")
    }


def sell(symbol: str, quantity: float) -> Dict:
    """
    Place a market SELL order.
    
    Args:
        symbol: e.g., "BTC-USDT" or "BTC"
        quantity: Amount to sell
    
    Returns:
        {
            "success": bool,
            "orderId": str,
            "symbol": str,
            "side": "sell",
            "quantity": float,
            "price": float,
            "total": float,
            "pnl": float (profit/loss in USDT),
            "pnl_pct": float (profit/loss %),
            "message": str
        }
    """
    exchange = get_exchange_instance()
    
    # Normalize symbol
    if not symbol.endswith("-USDT"):
        symbol = f"{symbol}-USDT"
    
    result = exchange.place_market_order(symbol, "sell", quantity)
    success = _is_success_status(result.get("status")) or _is_success_status(result.get("success"))
    
    return {
        "success": success,
        "orderId": result.get("orderId"),
        "symbol": symbol,
        "side": "sell",
        "quantity": quantity,
        "price": result.get("price", 0),
        "total": result.get("total", 0),
        "pnl": result.get("pnl", 0),
        "pnl_pct": result.get("pnl_pct", 0),
        "message": result.get("message", "Unknown error")
    }


def get_balance() -> Dict:
    """
    Get account balance and positions.
    
    Returns:
        {
            "usdt": float (available cash),
            "positions": {
                "BTC-USDT": {"qty": float, "entry_price": float, "cost_basis": float},
                ...
            },
            "total_value": float (USDT),
            "total_pnl": float (total realized P&L),
            "win_rate": float (% of winning trades),
            "trades_closed": int
        }
    """
    exchange = get_exchange_instance()
    return exchange.get_account_balance()


def get_positions() -> Dict:
    """
    Get all open positions.
    
    Returns:
        {
            "BTC-USDT": {
                "qty": float,
                "entry_price": float,
                "cost_basis": float,
                "entry_time": str (ISO datetime),
                "dca_levels": [...]
            },
            ...
        }
    """
    exchange = get_exchange_instance()
    balance = exchange.get_account_balance()
    return balance.get("positions", {})


def get_position(symbol: str) -> Optional[Dict]:
    """
    Get a specific position.
    
    Args:
        symbol: e.g., "BTC-USDT" or "BTC"
    
    Returns:
        Position dict or None if not held
    """
    if not symbol.endswith("-USDT"):
        symbol = f"{symbol}-USDT"
    
    positions = get_positions()
    return positions.get(symbol)


def get_current_price(symbol: str) -> Optional[float]:
    """Fetch current market price."""
    exchange = get_exchange_instance()
    return exchange.get_current_price(symbol)


def get_trade_history(symbol: str = None, limit: int = 100) -> List[Dict]:
    """
    Get closed trade history.
    
    Args:
        symbol: Filter by symbol (optional)
        limit: Max number of trades to return
    
    Returns:
        List of trade dicts with pnl, entry/exit prices, etc.
    """
    exchange = get_exchange_instance()
    return exchange.get_trade_history(symbol, limit)


def print_account_summary():
    """Print formatted account summary."""
    exchange = get_exchange_instance()
    exchange.print_summary()


def cancel_order(order_id: str) -> Dict:
    """Cancel a pending order."""
    exchange = get_exchange_instance()
    result = exchange.cancel_order(order_id)
    return {
        "success": result.get("status") == "SUCCESS",
        "message": result.get("message")
    }


# ============================================
# Configuration & Status
# ============================================

def get_status() -> Dict:
    """Get current exchange status."""
    balance = get_balance()
    pnl_pct = ((balance["total_value"] - 50.0) / 50.0 * 100) if balance["total_value"] > 0 else 0
    
    return {
        "mode": get_exchange_mode(),
        "usdt_balance": balance["usdt"],
        "total_value": balance["total_value"],
        "total_pnl": balance["total_pnl"],
        "total_pnl_pct": pnl_pct,
        "positions_open": len(balance["positions"]),
        "trades_closed": balance["trades_closed"],
        "win_rate": balance["win_rate"]
    }


def reset_simulator(initial_usdt: float = 50.0):
    """Reset the simulator to initial state."""
    global _exchange
    import os
    simulator_log = os.getenv("POWERTRADER_SIMULATOR_LOG")
    
    # Delete old log file
    if simulator_log and os.path.exists(simulator_log):
        os.remove(simulator_log)
    elif os.path.exists("simulator_trades.json"):
        os.remove("simulator_trades.json")
    
    # Create fresh instance
    _exchange = KuCoinSimulator(initial_usdt=initial_usdt, simulator_log=simulator_log, load_existing=False)
    print(f"✅ Simulator reset with ${initial_usdt:.2f} USDT")


if __name__ == "__main__":
    # Demo
    print("\n🚀 Exchange API Demo\n")
    
    initialize_exchange(mode="SIMULATOR", initial_usdt=50.0)
    
    print("📊 Initial Balance:")
    print_account_summary()
    
    print("\n📥 Buying 0.01 ETH...")
    result = buy("ETH", 0.01)
    print(f"✓ Buy result: {result['message']}")
    
    print("\n📊 After Buy:")
    print_account_summary()
    
    print("\n📊 Getting positions...")
    pos = get_position("ETH")
    if pos:
        print(f"ETH Position: {pos['qty']} coins @ ${pos['entry_price']:.2f}")
    
    print("\nStatus:", get_status())
