"""
KuCoin Trading Simulator
Mimics KuCoin API but operates on simulated account balance.
Uses real live prices for realistic backtesting.
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
import requests
import time
import math

_KUCOIN_API_BASE = "https://api.kucoin.com/api/v1"
_KUCOIN_SESSION = requests.Session()


class KuCoinSimulator:
    """Simulates KuCoin trading with simulated balance."""

    def __init__(self, initial_usdt: float = 50.0, simulator_log: str = None, load_existing: bool = True):
        """
        Args:
            initial_usdt: Starting USDT balance (e.g., £30-50 equivalent)
            simulator_log: Path to log trades (default: simulator_trades.json)
            load_existing: If False, always start from a clean in-memory state.
        """
        self.initial_balance = initial_usdt
        self.usdt_balance = initial_usdt
        self.positions = {}  # {symbol: {qty, cost_basis, entry_price, entry_time}}
        self.active_orders = {}  # {order_id: {symbol, side, qty, price, status, timestamp}}
        self.order_counter = 0
        default_log = os.getenv("POWERTRADER_SIMULATOR_LOG")
        if not default_log:
            exec_mode = (os.getenv("POWERTRADER_EXECUTION_MODE") or os.getenv("EXCHANGE_MODE") or "").strip().lower()
            if exec_mode in ("paper", "simulator", "true", "1", "yes"):
                default_log = os.path.join(os.getcwd(), "hub_data", "paper", "simulator_trades.json")
            else:
                default_log = "simulator_trades.json"
        self.simulator_log = simulator_log or default_log
        self.load_existing = bool(load_existing)
        self.closed_trades = []  # For P&L tracking

        self._load_state()
    
    def _load_state(self):
        """Load previous simulator state if exists."""
        if not self.load_existing:
            return
        if os.path.exists(self.simulator_log):
            try:
                with open(self.simulator_log, "r") as f:
                    data = json.load(f)
                    self.usdt_balance = data.get("usdt_balance", self.initial_balance)
                    self.positions = data.get("positions", {})
                    self.closed_trades = data.get("closed_trades", [])
                    self.order_counter = data.get("order_counter", 0)
            except:
                pass
    
    def _save_state(self):
        """Persist simulator state to JSON."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "usdt_balance": self.usdt_balance,
            "positions": self.positions,
            "closed_trades": self.closed_trades,
            "order_counter": self.order_counter,
            "total_pnl": sum(t.get("pnl", 0) for t in self.closed_trades),
            "win_count": len([t for t in self.closed_trades if t.get("pnl", 0) > 0]),
            "loss_count": len([t for t in self.closed_trades if t.get("pnl", 0) < 0])
        }
        with open(self.simulator_log, "w") as f:
            json.dump(data, f, indent=2)

    def reset_state(self, initial_usdt: Optional[float] = None, remove_log: bool = True) -> None:
        """Reset the simulator to a clean in-memory and on-disk state."""
        if initial_usdt is not None:
            self.initial_balance = float(initial_usdt)
        self.usdt_balance = float(self.initial_balance)
        self.positions = {}
        self.active_orders = {}
        self.order_counter = 0
        self.closed_trades = []

        if remove_log and os.path.exists(self.simulator_log):
            try:
                os.remove(self.simulator_log)
            except Exception:
                pass

    def get_ticker_price(self, symbol: str) -> Optional[float]:
        """Compatibility alias used by live orchestration code."""
        return self.get_current_price(symbol)

    def get_symbol_meta(self, symbol: str) -> Optional[Dict]:
        """Fetch live symbol metadata from KuCoin public API."""
        try:
            normalized = symbol if symbol.endswith("-USDT") else f"{symbol}-USDT"
            resp = _KUCOIN_SESSION.get("https://api.kucoin.com/api/v2/symbols", timeout=8)
            data = resp.json()
            if data.get("code") != "200000":
                return None
            for row in data.get("data", []):
                if row.get("symbol") == normalized:
                    return row
        except Exception:
            return None
        return None

    def get_account_overview(self) -> Dict:
        """Compatibility helper matching the live client shape."""
        balance = self.get_account_balance()
        return {"total_value_usdt": balance.get("total_value", 0.0)}

    def place_market_buy_usdt(self, symbol: str, usdt_amount: float) -> Dict:
        """Place a simulated market buy using USDT funds."""
        price = self.get_current_price(symbol)
        if price is None or price <= 0:
            return {"orderId": None, "status": "error", "message": f"Could not fetch price for {symbol}"}

        meta = self.get_symbol_meta(symbol) or {}
        base_inc = float(meta.get("baseIncrement", 0) or 0)
        quote_min = float(meta.get("quoteMinSize", 0) or 0)
        quote_inc = float(meta.get("quoteIncrement", 0) or 0)

        spend = float(usdt_amount)
        if quote_inc > 0:
            spend = math.floor(spend / quote_inc) * quote_inc
        if quote_min > 0 and spend < quote_min:
            return {"orderId": None, "status": "error", "message": f"Spend {spend} below quoteMinSize {quote_min}"}

        qty = spend / price
        if base_inc > 0:
            qty = math.floor(qty / base_inc) * base_inc
        if qty <= 0:
            return {"orderId": None, "status": "error", "message": "Rounded size is zero"}

        result = self.place_market_order(symbol, "buy", qty)
        if result.get("status") == "DONE":
            result["status"] = "success"
        return result
    
    def get_current_price(self, symbol: str) -> float:
        """Fetch real current price from KuCoin public API."""
        try:
            # Normalize symbol (e.g., BTC -> BTC-USDT)
            if not symbol.endswith("-USDT"):
                symbol = f"{symbol}-USDT"
            
            resp = _KUCOIN_SESSION.get(
                f"{_KUCOIN_API_BASE}/market/orderbook/level1?symbol={symbol}",
                timeout=5
            )
            data = resp.json()
            
            if data.get("code") == "200000" and data.get("data"):
                price = float(data["data"].get("price", 0))
                return price if price > 0 else None
            return None
        except Exception as e:
            print(f"❌ Error fetching price for {symbol}: {e}")
            return None
    
    def place_market_order(self, symbol: str, side: str, qty: float, 
                          order_type: str = "MARKET") -> Dict:
        """
        Place a simulated market order.
        
        Args:
            symbol: e.g., "BTC-USDT"
            side: "buy" or "sell"
            qty: Quantity to trade
            order_type: "MARKET" or "LIMIT" (default: MARKET)
        
        Returns:
            {"orderId": str, "status": str, "message": str}
        """
        side = side.lower()
        
        # Get real current price
        price = self.get_current_price(symbol)
        if price is None:
            return {"orderId": None, "status": "error", "message": f"Could not fetch price for {symbol}"}
        
        # Calculate total cost
        total_cost = qty * price
        
        if side == "buy":
            if total_cost > self.usdt_balance:
                return {
                    "orderId": None,
                    "status": "error",
                    "message": f"Insufficient balance. Need {total_cost:.2f} USDT, have {self.usdt_balance:.2f}"
                }
            
            # Deduct from balance
            self.usdt_balance -= total_cost
            
            # Track position
            if symbol not in self.positions:
                self.positions[symbol] = {
                    "qty": 0,
                    "cost_basis": 0,
                    "entry_price": 0,
                    "entry_time": datetime.now().isoformat(),
                    "dca_levels": []
                }
            
            pos = self.positions[symbol]
            old_qty = pos["qty"]
            new_qty = old_qty + qty
            new_cost = pos["cost_basis"] + total_cost
            
            pos["qty"] = new_qty
            pos["cost_basis"] = new_cost
            pos["entry_price"] = new_cost / new_qty if new_qty > 0 else 0
            pos["last_update"] = datetime.now().isoformat()
            pos["dca_levels"].append({
                "qty": qty,
                "price": price,
                "cost": total_cost,
                "timestamp": datetime.now().isoformat()
            })
            
            message = f"BUY {qty} {symbol} @ ${price:.4f} = ${total_cost:.2f}"
        
        elif side == "sell":
            if symbol not in self.positions or self.positions[symbol]["qty"] < qty:
                return {
                    "orderId": None,
                    "status": "error",
                    "message": f"Don't have {qty} of {symbol} to sell"
                }
            
            # Calculate P&L
            pos = self.positions[symbol]
            cost_per_unit = pos["cost_basis"] / pos["qty"] if pos["qty"] > 0 else 0
            pnl = (price - cost_per_unit) * qty
            pnl_pct = (pnl / (cost_per_unit * qty) * 100) if (cost_per_unit * qty) > 0 else 0
            
            # Add funds back to balance
            self.usdt_balance += total_cost
            
            # Update position
            pos["qty"] -= qty
            pos["cost_basis"] -= cost_per_unit * qty
            pos["last_update"] = datetime.now().isoformat()
            
            # Log closed trade
            self.closed_trades.append({
                "symbol": symbol,
                "qty": qty,
                "entry_price": cost_per_unit,
                "exit_price": price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "timestamp": datetime.now().isoformat()
            })
            
            # Delete position if fully closed
            if pos["qty"] <= 0:
                del self.positions[symbol]
            
            message = f"SELL {qty} {symbol} @ ${price:.4f} = ${total_cost:.2f} | P&L: ${pnl:.2f} ({pnl_pct:+.2f}%)"
        
        else:
            return {"orderId": None, "status": "error", "message": f"Invalid side: {side}"}
        
        # Generate order ID
        self.order_counter += 1
        order_id = f"SIM-{self.order_counter}-{int(time.time())}"
        
        # Track order as filled immediately (market order)
        self.active_orders[order_id] = {
            "orderId": order_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "status": "DONE",
            "timestamp": datetime.now().isoformat(),
            "pnl": pnl if side == "sell" else None
        }
        
        # Persist state
        self._save_state()
        
        return {
            "orderId": order_id,
            "status": "success",
            "message": message,
            "price": price,
            "total": total_cost
        }
    
    def cancel_order(self, order_id: str) -> Dict:
        """Cancel a simulated order (market orders can't be cancelled)."""
        if order_id not in self.active_orders:
            return {"status": "FAILED", "message": f"Order {order_id} not found"}
        
        order = self.active_orders[order_id]
        if order["status"] == "DONE":
            return {"status": "FAILED", "message": "Market orders cannot be cancelled after fill"}
        
        del self.active_orders[order_id]
        return {"status": "SUCCESS", "message": f"Order {order_id} cancelled"}
    
    def get_account_balance(self) -> Dict:
        """Get simulated account balance and positions."""
        total_value = self.usdt_balance
        
        # Add value of open positions at current prices
        for symbol, pos in self.positions.items():
            current_price = self.get_current_price(symbol)
            if current_price:
                position_value = pos["qty"] * current_price
                total_value += position_value
        
        return {
            "usdt": self.usdt_balance,
            "positions": self.positions,
            "total_value": total_value,
            "total_pnl": sum(t.get("pnl", 0) for t in self.closed_trades),
            "win_rate": (len([t for t in self.closed_trades if t.get("pnl", 0) > 0]) / 
                        len(self.closed_trades) * 100) if self.closed_trades else 0,
            "trades_closed": len(self.closed_trades)
        }
    
    def get_order_status(self, order_id: str) -> Dict:
        """Get status of a simulated order."""
        if order_id not in self.active_orders:
            return {"status": "UNKNOWN", "message": f"Order {order_id} not found"}

        order = self.active_orders[order_id]
        return {
            "orderId": order.get("orderId"),
            "status": order.get("status"),
            "side": order.get("side"),
            "symbol": order.get("symbol"),
            "size": order.get("qty"),
            "dealSize": order.get("qty"),
            "dealFunds": (order.get("qty", 0) or 0) * (order.get("price", 0) or 0),
            "price": order.get("price"),
            "createdAtIso": order.get("timestamp"),
        }
    
    def get_trade_history(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        """Get closed trade history."""
        history = self.closed_trades
        if symbol:
            history = [t for t in history if t["symbol"] == symbol]
        return history[-limit:]
    
    def print_summary(self):
        """Print account summary."""
        balance = self.get_account_balance()
        print("\n" + "="*60)
        print("🎰 KuCoin Simulator Account Summary")
        print("="*60)
        print(f"💵 USDT Balance: ${balance['usdt']:.2f}")
        print(f"📊 Open Positions: {len(balance['positions'])}")
        for symbol, pos in balance['positions'].items():
            current_price = self.get_current_price(symbol)
            if current_price:
                unrealized = (current_price - pos['entry_price']) * pos['qty']
                print(f"   • {symbol}: {pos['qty']:.4f} @ ${pos['entry_price']:.4f} (unrealized: ${unrealized:+.2f})")
        print(f"💰 Total Account Value: ${balance['total_value']:.2f}")
        print(f"📈 Total P&L: ${balance['total_pnl']:+.2f}")
        print(f"✅ Win Rate: {balance['win_rate']:.1f}% ({len([t for t in self.closed_trades if t.get('pnl', 0) > 0])}/{len(self.closed_trades)} trades)")
        print("="*60 + "\n")


def create_simulator(initial_usdt: float = 50.0) -> KuCoinSimulator:
    """Factory function to create a simulator instance."""
    return KuCoinSimulator(initial_usdt)


if __name__ == "__main__":
    # Demo: Create simulator with £30 (about 30 USDT)
    sim = KuCoinSimulator(initial_usdt=50.0)
    
    print("🚀 KuCoin Simulator Demo")
    print("\nStarting balance: $50 USDT")
    
    # Simulate a BTC buy
    print("\n📥 Placing BUY order for 0.001 BTC...")
    result = sim.place_market_order("BTC-USDT", "buy", 0.001)
    print(f"✓ {result['message']}")
    
    # Simulate an ETH buy
    print("\n📥 Placing BUY order for 0.01 ETH...")
    result = sim.place_market_order("ETH-USDT", "buy", 0.01)
    print(f"✓ {result['message']}")
    
    # Show account
    sim.print_summary()
    
    # Simulate a sell (if profitable)
    print("\n📤 Placing SELL order for 0.005 BTC...")
    result = sim.place_market_order("BTC-USDT", "sell", 0.005)
    print(f"✓ {result['message']}")
    
    # Final summary
    sim.print_summary()
