"""
Real KuCoin API Integration (Authenticated)
Handles actual live trading with KuCoin using API credentials.
"""

import os
import time
import hmac
import hashlib
import base64
import math
import requests
from typing import Any, Dict, Optional, List
import json
import logging
from collections import deque
from threading import Lock
from datetime import datetime
from urllib.parse import urlencode
from decimal import Decimal, ROUND_DOWN, InvalidOperation

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed, will fallback to system env vars only
    pass

logger = logging.getLogger(__name__)

# Local minimal logging directory helpers (avoid dependency on external logging_config)
LOG_DIR = os.path.join(os.getcwd(), "logs")

def ensure_log_directory():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass


class RateLimiter:
    """
    Thread-safe rate limiter using sliding window algorithm.
    Prevents API rate limit violations by enforcing maximum calls per period.
    """
    
    def __init__(self, max_calls: int, period: float):
        """
        Initialize rate limiter.
        
        Args:
            max_calls: Maximum number of calls allowed
            period: Time period in seconds
        """
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = Lock()
        logger.info(f"Rate limiter initialized: {max_calls} calls per {period}s")
    
    def wait_if_needed(self):
        """Block execution if rate limit would be exceeded."""
        with self.lock:
            now = time.time()
            
            # Remove expired calls outside the time window
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            
            # Check if we're at the limit
            if len(self.calls) >= self.max_calls:
                # Calculate how long to wait
                sleep_time = self.period - (now - self.calls[0]) + 0.1  # +0.1s buffer
                if sleep_time > 0:
                    logger.debug(f"Rate limit reached, sleeping {sleep_time:.2f}s")
                    time.sleep(sleep_time)
                    # Re-clean after sleep
                    now = time.time()
                    while self.calls and self.calls[0] < now - self.period:
                        self.calls.popleft()
            
            # Record this call
            self.calls.append(now)

    def get_usage_snapshot(self) -> Dict[str, float]:
        """Get current rate limit usage snapshot.

        Returns:
            Dict with max_calls, period_seconds, current_calls, remaining_calls,
            and reset_in_seconds.
        """
        with self.lock:
            now = time.time()
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()

            current_calls = len(self.calls)
            remaining_calls = max(self.max_calls - current_calls, 0)
            reset_in_seconds = 0.0
            if self.calls:
                reset_in_seconds = max(self.period - (now - self.calls[0]), 0.0)

            return {
                "max_calls": float(self.max_calls),
                "period_seconds": float(self.period),
                "current_calls": float(current_calls),
                "remaining_calls": float(remaining_calls),
                "reset_in_seconds": float(reset_in_seconds)
            }


class KuCoinAPI:
    """Authenticated KuCoin trading API client."""
    
    BASE_URL = "https://api.kucoin.com"
    
    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        """
        Initialize KuCoin API client.
        
        Args:
            api_key: KuCoin API key
            api_secret: KuCoin API secret
            api_passphrase: KuCoin API passphrase
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase  # v1 uses plain passphrase
        self.session = requests.Session()
        
        # Rate limiters to prevent API bans
        # KuCoin limits: Private API = 30 requests/3 seconds, Public API = 100 requests/10 seconds
        self.private_limiter = RateLimiter(max_calls=30, period=3.0)
        self.public_limiter = RateLimiter(max_calls=100, period=10.0)
        self._last_usage_write_ts = 0.0
        self._last_private_call_ts = 0.0
        self._last_public_call_ts = 0.0
        self._time_offset_ms = 0
        self._last_time_sync_ts = 0.0
        # Backwards-compatible circuit-breaker and rate-limit fields
        # Older code/tests reference these internal attributes; keep aliases
        self._rate_limit_violations = 0
        self._max_violations_before_break = 3
        self._circuit_breaker_duration = 60.0
        self._circuit_breaker_until = 0.0
        logger.info("KuCoin API client initialized with rate limiting")
    
    def _generate_signature(self, timestamp: str, method: str, endpoint: str, body: str = "") -> str:
        """Generate HMAC-SHA256 signature for KuCoin API."""
        message = timestamp + method + endpoint + body
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(signature).decode()
    
    def _get_timestamp(self) -> str:
        """Get current timestamp in milliseconds.

        Returns:
            Timestamp string adjusted for KuCoin server time when available.
        """
        now = time.time()
        if now - self._last_time_sync_ts > 300:
            self._sync_server_time()
        return str(int((now * 1000) + self._time_offset_ms))

    def _sync_server_time(self) -> None:
        """Sync local time offset against KuCoin server time.

        Note:
            Uses public endpoint and best-effort; failures do not raise.
        """
        try:
            resp = self.session.get(f"{self.BASE_URL}/api/v1/timestamp", timeout=5)
            data = resp.json()
            if data.get("code") == "200000":
                server_ms = int(data.get("data", 0) or 0)
                local_ms = int(time.time() * 1000)
                self._time_offset_ms = server_ms - local_ms
                self._last_time_sync_ts = time.time()
                logger.info("Synced KuCoin server time offset: %sms", self._time_offset_ms)
            else:
                logger.warning("Failed to sync KuCoin time: %s", data)
        except Exception as e:
            logger.error("Failed to sync KuCoin time: %s", e, exc_info=True)
    
    def _request(self, method: str, endpoint: str, body: str = "", params: Dict = None, retry: bool = True) -> Dict:
        """
        Make authenticated request to KuCoin API with rate limiting.
        
        Args:
            method: GET, POST, DELETE
            endpoint: API endpoint (e.g., "/api/v1/accounts")
            body: JSON body for POST requests
            params: Query parameters
        
        Returns:
            Response JSON
        """
        # Short-circuit if circuit breaker is active (compatibility with older logic)
        if getattr(self, '_circuit_breaker_until', 0.0) and time.time() < self._circuit_breaker_until:
            logger.warning("Circuit breaker active - blocking request")
            return {"code": "circuit_breaker", "msg": "Circuit breaker active"}

        # Apply rate limiting based on endpoint type
        is_private = any(path in endpoint for path in ["/api/v1/accounts", "/api/v1/orders", "/api/v2/accounts"])
        
        if is_private:
            self.private_limiter.wait_if_needed()
            self._last_private_call_ts = time.time()
        else:
            self.public_limiter.wait_if_needed()
            self._last_public_call_ts = time.time()

        self._maybe_write_rate_limit_status()
        
        timestamp = self._get_timestamp()

        signature_endpoint = endpoint
        if params and method == "GET":
            query_string = urlencode(params)
            signature_endpoint = f"{endpoint}?{query_string}"

        signature = self._generate_signature(timestamp, method, signature_endpoint, body)
        
        headers = {
            "KC-API-SIGN": signature,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-KEY": self.api_key,
            "KC-API-PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json"
        }
        
        url = self.BASE_URL + endpoint
        
        try:
            if method == "GET":
                resp = self.session.get(url, headers=headers, params=params, timeout=10)
            elif method == "POST":
                resp = self.session.post(url, headers=headers, data=body, timeout=10)
            elif method == "DELETE":
                resp = self.session.delete(url, headers=headers, params=params, timeout=10)
            else:
                raise ValueError(f"Unknown method: {method}")
            
            result = resp.json()
            
            # Log successful requests at debug level
            logger.debug(f"{method} {endpoint} -> {result.get('code', 'unknown')}")
            
            # Log errors at appropriate levels
            if result.get('code') != '200000':
                logger.warning(f"API returned error: {method} {endpoint} -> {result}")
                # Handle rate limit response (compatibility)
                if result.get('code') in ('429000', 429):
                    # Increment violation count and backoff
                    try:
                        self._handle_rate_limit_violation()
                    except Exception:
                        # Best-effort shim if method not present
                        self._rate_limit_violations = getattr(self, '_rate_limit_violations', 0) + 1
                    return result
                if result.get('code') == '400002' and retry:
                    logger.warning("Resyncing KuCoin time due to invalid timestamp...")
                    self._sync_server_time()
                    return self._request(method, endpoint, body=body, params=params, retry=False)
            
            return result
        
        except requests.exceptions.Timeout as e:
            logger.error(f"API timeout: {method} {endpoint} - {e}")
            return {"code": "timeout", "msg": f"Request timeout: {str(e)}"}
        
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error: {method} {endpoint} - {e}")
            return {"code": "connection_error", "msg": f"Connection failed: {str(e)}"}
        
        except Exception as e:
            logger.error(f"Unexpected API error: {method} {endpoint}", exc_info=True)
            return {"code": "500", "msg": str(e)}

    def _maybe_write_rate_limit_status(self) -> None:
        """Persist current rate limit usage to a shared log file.

        This is best-effort and throttled to avoid excessive I/O. The snapshot
        reflects usage within the local process and is intended for dashboard
        visibility rather than strict accounting.
        """
        now = time.time()
        if now - self._last_usage_write_ts < 1.0:
            return

        self._last_usage_write_ts = now
        try:
            ensure_log_directory()
            usage_path = os.path.join(LOG_DIR, "kucoin_rate_limits.json")
            existing: Dict[str, Any] = {}
            if os.path.exists(usage_path):
                try:
                    with open(usage_path, "r", encoding="utf-8") as f:
                        existing = json.load(f) or {}
                except Exception:
                    existing = {}

            payload = {
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "source_pid": os.getpid(),
                "private": existing.get("private"),
                "public": existing.get("public")
            }

            if now - self._last_private_call_ts < 60:
                payload["private"] = self.private_limiter.get_usage_snapshot()

            if now - self._last_public_call_ts < 60:
                payload["public"] = self.public_limiter.get_usage_snapshot()

            with open(usage_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to write rate limit status: {e}")

    # --------------------- Compatibility shims ---------------------
    def _handle_rate_limit_violation(self) -> None:
        """Handle a rate limit violation with exponential backoff and circuit breaker.

        This restores behavior expected by older tests and callers that relied on
        internal methods/fields. It increases the violation counter, sleeps for
        an exponential backoff (2^n seconds), and activates the circuit breaker
        if violations exceed the configured maximum.
        """
        self._rate_limit_violations = getattr(self, '_rate_limit_violations', 0) + 1
        backoff = min(2 ** max(1, self._rate_limit_violations), 60)
        try:
            time.sleep(backoff)
        except Exception:
            pass

        if self._rate_limit_violations >= getattr(self, '_max_violations_before_break', 3):
            self._circuit_breaker_until = time.time() + getattr(self, '_circuit_breaker_duration', 60.0)

    def _check_circuit_breaker(self) -> bool:
        """Return True if the circuit breaker is currently active.

        Also reset violations when the breaker has expired to restore
        expected older behavior used by tests.
        """
        until = float(getattr(self, '_circuit_breaker_until', 0.0) or 0.0)
        now = time.time()
        if now < until:
            return True
        # expired -> reset state
        if until and now >= until:
            self._rate_limit_violations = 0
            self._circuit_breaker_until = 0.0
        return False

    def get_fills_history(self, symbol: str = None) -> List[Dict]:
        """Backward-compatible alias for `get_trade_history` used in tests."""
        return self.get_trade_history(symbol)

    
    def get_account_balance(self) -> Dict:
        """
        Get account balance.
        
        Returns:
            {
                "usdt": float (USDT balance),
                "positions": {symbol: {qty, entry_price, ...}},
                "total_value": float
            }
        """
        try:
            # Get accounts
            resp = self._request("GET", "/api/v1/accounts")
            
            if resp.get("code") != "200000":
                return {"error": resp.get("msg", "Unknown error")}
            
            accounts = resp.get("data", [])
            
            # Find USDT account
            usdt_balance = 0.0
            for account in accounts:
                if account.get("currency") == "USDT" and account.get("type") == "trade":
                    usdt_balance = float(account.get("balance", 0))
            
            # Get holdings
            positions = {}
            for account in accounts:
                currency = account.get("currency", "").strip().upper()
                if currency and currency != "USDT":
                    qty = float(account.get("balance", 0))
                    if qty > 0:
                        positions[f"{currency}-USDT"] = {
                            "qty": qty,
                            "entry_price": 0,  # KuCoin doesn't track this, would need to calc from trades
                            "cost_basis": 0
                        }
            
            return {
                "usdt": usdt_balance,
                "positions": positions,
                "total_value": usdt_balance  # Plus value of positions (simplified)
            }
        
        except Exception as e:
            print(f"❌ Error getting balance: {e}")
            return {"error": str(e)}
    
    def place_market_order(self, symbol: str, side: str, size: float) -> Dict:
        """
        Place a market order.
        
        Args:
            symbol: Trading pair (e.g., "BTC-USDT")
            side: "buy" or "sell"
            size: Quantity to trade
        
        Returns:
            {
                "orderId": str,
                "status": "success" or "error",
                "message": str
            }
        """
        try:
            normalized_side = side.lower()
            size_to_send = float(size)
            size_increment = 0.0

            meta = self.get_symbol_meta(symbol) or {}
            base_inc = float(meta.get("baseIncrement", 0) or 0)
            base_min = float(meta.get("baseMinSize", 0) or 0)
            if base_inc and base_inc > 0:
                size_to_send = self._round_down_increment(size_to_send, base_inc)
                size_increment = base_inc

            if size_to_send <= 0:
                return {
                    "orderId": None,
                    "status": "error",
                    "message": "Rounded size is zero"
                }

            if base_min and size_to_send < base_min:
                return {
                    "orderId": None,
                    "status": "error",
                    "message": f"Size {size_to_send} below baseMinSize {base_min}"
                }

            body = json.dumps({
                "clientOid": f"{int(time.time() * 1000)}",
                "side": normalized_side,
                "symbol": symbol,
                "type": "market",
                "size": self._format_incremented_value(size_to_send, size_increment)
            })
            
            resp = self._request("POST", "/api/v1/orders", body=body)
            
            if resp.get("code") == "200000":
                return {
                    "orderId": resp.get("data", {}).get("orderId"),
                    "status": "success",
                    "message": f"Order placed: {normalized_side.upper()} {size_to_send} {symbol}"
                }
            else:
                return {
                    "orderId": None,
                    "status": "error",
                    "message": resp.get("msg", "Unknown error")
                }
        
        except Exception as e:
            return {
                "orderId": None,
                "status": "error",
                "message": str(e)
            }
    
    def get_order_status(self, order_id: str) -> Dict:
        """Get status of an order.

        Args:
            order_id: KuCoin order ID.

        Returns:
            Dict with order details including dealFunds and dealSize when available.

        Raises:
            None.

        Note:
            Market orders may report price as 0; use dealFunds/dealSize for avg.
        """
        try:
            resp = self._request("GET", f"/api/v1/orders/{order_id}")
            
            if resp.get("code") == "200000":
                order = resp.get("data", {})
                created_at = order.get("createdAt") or order.get("created_at")
                created_iso = None
                try:
                    # KuCoin returns milliseconds since epoch for createdAt in some endpoints
                    if isinstance(created_at, (int, float)) and created_at > 0:
                        created_iso = datetime.utcfromtimestamp(int(created_at) / 1000).isoformat() + "Z"
                    elif isinstance(created_at, str) and created_at.isdigit():
                        created_iso = datetime.utcfromtimestamp(int(created_at) / 1000).isoformat() + "Z"
                    elif isinstance(created_at, str) and created_at:
                        # If already ISO-like, keep as-is
                        created_iso = created_at
                except Exception:
                    created_iso = None

                return {
                    "orderId": order.get("id"),
                    "status": order.get("isActive"),
                    "side": order.get("side"),
                    "symbol": order.get("symbol"),
                    "size": order.get("size"),
                    "dealSize": order.get("dealSize"),
                    "dealFunds": order.get("dealFunds"),
                    "price": order.get("price"),
                    "createdAt": created_at,
                    "createdAtIso": created_iso
                }
            else:
                return {"status": "error", "message": resp.get("msg")}
        
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def cancel_order(self, order_id: str) -> Dict:
        """Cancel an order."""
        try:
            resp = self._request("DELETE", f"/api/v1/orders/{order_id}")
            
            if resp.get("code") == "200000":
                return {"status": "success", "message": f"Order {order_id} cancelled"}
            else:
                return {"status": "error", "message": resp.get("msg")}
        
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    def get_trade_history(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """Get recent trades.

        Args:
            symbol: Optional symbol filter (e.g. 'BTC-USDT').
            limit: Maximum number of fills to return.
        """
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol
            
            resp = self._request("GET", "/api/v1/fills", params=params)
            
            if resp.get("code") == "200000":
                items = resp.get("data", {}).get("items", [])
                try:
                    limit = int(limit)
                except Exception:
                    limit = 100
                if limit > 0:
                    return items[:limit]
                return items
            else:
                return []
        
        except Exception as e:
            print(f"❌ Error getting trades: {e}")
            return []

    # ----------------- Backwards-compatible fills and PnL helpers -----------------
    def get_fills_history(self, symbol: str = None, start_time: int = None, end_time: int = None) -> List[Dict]:
        """Fetch fills history with pagination support.

        Args:
            symbol: Optional symbol filter (e.g., 'BTC-USDT')
            start_time: Optional start timestamp in ms (passed as startAt)
            end_time: Optional end timestamp in ms (passed as endAt)

        Returns:
            List of fill dicts across all pages.
        """
        params: Dict[str, Any] = {}
        if symbol:
            params['symbol'] = symbol
        if start_time is not None:
            params['startAt'] = start_time
        if end_time is not None:
            params['endAt'] = end_time

        page = 1
        all_items: List[Dict] = []
        while True:
            params['page'] = page
            resp = self._request('GET', '/api/v1/fills', params=params)
            if resp.get('code') != '200000':
                break
            data = resp.get('data', {})
            items = data.get('items', [])
            all_items.extend(items)
            total_page = int(data.get('totalPage', 1) or 1)
            if page >= total_page:
                break
            page += 1

        return all_items

    def get_account_overview(self) -> Dict:
        """Convenience wrapper that combines balances and current prices.

        Returns:
            Dict containing `usdt_balance`, `total_value_usdt`, and `positions` with `current_price`.
        """
        bal = self.get_account_balance() or {}
        usdt = float(bal.get('usdt', 0.0) or 0.0)
        positions = bal.get('positions', {}) or {}
        total = usdt
        pos_out: Dict[str, Dict] = {}
        for sym, info in positions.items():
            qty = float(info.get('qty', 0) or 0)
            price = self.get_ticker_price(sym) or 0.0
            pos_out[sym] = {'qty': qty, 'current_price': price}
            total += qty * price

        return {'usdt_balance': usdt, 'total_value_usdt': total, 'positions': pos_out}

    def calculate_realized_pnl(
        self,
        symbol: str = None,
        start_time: int = None,
        end_time: int = None,
    ) -> Dict:
        """Calculate realized PnL using FIFO matching of fills.

        Returns:
            Dict with keys: `num_completed_cycles`, `total_realized_pnl_usdt`,
            `total_fees_usdt`, and `net_pnl_usdt`.
        """
        fills = self.get_fills_history(symbol=symbol, start_time=start_time, end_time=end_time)
        # Ensure chronological order by createdAt when present
        try:
            fills_sorted = sorted(fills, key=lambda x: x.get('createdAt', 0))
        except Exception:
            fills_sorted = fills

        buy_queues: Dict[str, List[Dict]] = {}
        total_fees = 0.0
        total_realized = 0.0
        total_trades = 0
        total_cycles = 0
        by_symbol: Dict[str, Dict[str, Any]] = {}

        for f in fills_sorted:
            sym = f.get('symbol')
            side = f.get('side')
            price = float(f.get('price', 0) or 0)
            size = float(f.get('size', 0) or 0)
            fee = float(f.get('fee', 0) or 0)

            total_trades += 1
            total_fees += fee

            if sym not in by_symbol:
                by_symbol[sym] = {
                    'num_trades': 0,
                    'num_cycles': 0,
                    'open_buys': 0,
                    'realized_pnl_usdt': 0.0
                }

            by_symbol[sym]['num_trades'] += 1

            if side and side.lower() == 'buy':
                buy_queues.setdefault(sym, []).append({
                    'size': size,
                    'price': price,
                    'fee': fee
                })
                by_symbol[sym]['open_buys'] = len(buy_queues[sym])

            elif side and side.lower() == 'sell':
                remain = size
                queue = buy_queues.get(sym, [])
                while remain > 0 and queue:
                    lot = queue[0]
                    take = min(remain, lot['size'])
                    cost = take * lot['price']
                    proceeds = take * price
                    realized = proceeds - cost
                    total_realized += realized
                    by_symbol[sym]['realized_pnl_usdt'] += realized
                    # reduce lot
                    lot['size'] -= take
                    remain -= take
                    if lot['size'] <= 0:
                        queue.pop(0)
                by_symbol[sym]['open_buys'] = len(queue)
                by_symbol[sym]['num_cycles'] += 1
                total_cycles += 1

        net = total_realized - total_fees
        return {
            'num_trades': total_trades,
            'num_completed_cycles': total_cycles,
            'total_realized_pnl_usdt': total_realized,
            'total_fees_usdt': total_fees,
            'net_pnl_usdt': net,
            'by_symbol': by_symbol
        }

    def inner_transfer(self, currency: str, amount: float, from_acct: str = "main", to_acct: str = "trade") -> Dict:
        """Transfer funds between accounts (e.g., main -> trade)."""
        body = json.dumps({
            "clientOid": f"{int(time.time() * 1000)}",
            "from": from_acct,
            "to": to_acct,
            "currency": currency.upper(),
            "amount": str(amount)
        })
        resp = self._request("POST", "/api/v2/accounts/inner-transfer", body=body)
        return resp

    # --- Convenience helpers for sizing and metadata ---
    def _public_get(self, endpoint: str, params: Dict = None) -> Dict:
        try:
            url = self.BASE_URL + endpoint
            resp = self.session.get(url, params=params, timeout=10)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def get_ticker_price(self, symbol: str) -> Optional[float]:
        data = self._public_get("/api/v1/market/orderbook/level1", params={"symbol": symbol})
        try:
            if data.get("code") == "200000" and data.get("data"):
                price = float(data["data"].get("price", 0))
                return price if price > 0 else None
        except Exception:
            return None
        return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Backward-compatible alias used by the trader and exchange wrapper."""
        return self.get_ticker_price(symbol)

    def get_symbol_meta(self, symbol: str) -> Optional[Dict]:
        data = self._public_get("/api/v2/symbols")
        if data.get("code") != "200000":
            return None
        for row in data.get("data", []):
            if row.get("symbol") == symbol:
                return row
        return None

    @staticmethod
    def _round_down_increment(value: float, increment: float) -> float:
        if increment <= 0:
            return value
        try:
            value_dec = Decimal(str(value))
            increment_dec = Decimal(str(increment))
            return float((value_dec / increment_dec).to_integral_value(rounding=ROUND_DOWN) * increment_dec)
        except (InvalidOperation, ValueError):
            return math.floor(value / increment) * increment

    @staticmethod
    def _format_incremented_value(value: float, increment: float) -> str:
        if increment <= 0:
            return format(value, ".12f").rstrip("0").rstrip(".")

        try:
            value_dec = Decimal(str(value))
            increment_dec = Decimal(str(increment))
            rounded = (value_dec / increment_dec).to_integral_value(rounding=ROUND_DOWN) * increment_dec
            places = max(0, -increment_dec.normalize().as_tuple().exponent)
            text = f"{rounded:.{places}f}"
            return text.rstrip("0").rstrip(".") if "." in text else text
        except (InvalidOperation, ValueError):
            return format(value, ".12f").rstrip("0").rstrip(".")

    def place_market_buy_usdt(self, symbol: str, usdt_amount: float) -> Dict:
        """Place a market buy sized by quote (USDT) using funds param (KuCoin style)."""
        price = self.get_ticker_price(symbol)
        if price is None:
            return {"status": "error", "message": f"Could not fetch price for {symbol}"}

        meta = self.get_symbol_meta(symbol) or {}
        base_inc = float(meta.get("baseIncrement", 0) or 0)
        base_min = float(meta.get("baseMinSize", 0) or 0)
        quote_min = float(meta.get("quoteMinSize", 0) or 0)
        quote_inc = float(meta.get("quoteIncrement", 0) or 0)

        # Ensure meets min quote
        if quote_min and usdt_amount < quote_min:
            return {"status": "error", "message": f"Spend {usdt_amount} below quoteMinSize {quote_min}"}

        # Align funds to quote increment if provided
        if quote_inc and quote_inc > 0:
            usdt_amount = self._round_down_increment(usdt_amount, quote_inc)

        # Also sanity-check base size against baseMin/baseInc
        est_size = usdt_amount / price
        if base_min and est_size < base_min:
            return {"status": "error", "message": f"Size {est_size} below baseMinSize {base_min}"}
        if base_inc and base_inc > 0:
            est_size = self._round_down_increment(est_size, base_inc)
            if est_size <= 0:
                return {"status": "error", "message": "Rounded size is zero"}

        body = json.dumps({
            "clientOid": f"{int(time.time() * 1000)}",
            "side": "buy",
            "symbol": symbol,
            "type": "market",
            "funds": f"{usdt_amount:.8f}"
        })

        resp = self._request("POST", "/api/v1/orders", body=body)
        if resp.get("code") == "200000":
            return {
                "orderId": resp.get("data", {}).get("orderId"),
                "status": "success",
                "message": f"Order placed: BUY funds {usdt_amount} {symbol}"
            }
        return {
            "orderId": None,
            "status": "error",
            "message": resp.get("msg", "Unknown error")
        }


def load_kucoin_credentials() -> tuple:
    """
    Load KuCoin API credentials from environment variables or kucoin_keys.txt file.
    
    Environment variables take precedence over file-based credentials for security.
    This allows:
    - Production deployments to use environment variables
    - Local development to use file-based credentials
    
    Environment variables:
        KUCOIN_API_KEY: Your KuCoin API key
        KUCOIN_API_SECRET: Your KuCoin API secret
        KUCOIN_API_PASSPHRASE: Your KuCoin API passphrase
    
    Returns:
        Tuple[Optional[str], Optional[str], Optional[str]]: 
            (api_key, api_secret, api_passphrase) or (None, None, None) if not found
    """
    # Try environment variables first (preferred method)
    api_key = os.getenv('KUCOIN_API_KEY')
    api_secret = os.getenv('KUCOIN_API_SECRET')
    api_passphrase = os.getenv('KUCOIN_API_PASSPHRASE')
    
    if api_key and api_secret and api_passphrase:
        logger.info("KuCoin credentials loaded from environment variables")
        return api_key, api_secret, api_passphrase
    
    # Fallback to file for local development
    try:
        with open("kucoin_keys.txt", "r") as f:
            lines = f.readlines()
        
        credentials = {}
        for line in lines:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                credentials[key.strip()] = value.strip()
        
        api_key = credentials.get("API_KEY")
        api_secret = credentials.get("API_SECRET")
        api_passphrase = credentials.get("API_PASSPHRASE")
        
        if api_key and api_secret and api_passphrase:
            logger.warning("KuCoin credentials loaded from kucoin_keys.txt file. "
                          "Consider using environment variables for production.")
            return api_key, api_secret, api_passphrase
        else:
            print("❌ KuCoin credentials incomplete in kucoin_keys.txt")
            print("   Required fields: API_KEY, API_SECRET, API_PASSPHRASE")
            return None, None, None
    
    except FileNotFoundError:
        print("❌ KuCoin credentials not found.")
        print("\nOption 1 (Recommended): Set environment variables:")
        print("  export KUCOIN_API_KEY=your_key")
        print("  export KUCOIN_API_SECRET=your_secret")
        print("  export KUCOIN_API_PASSPHRASE=your_passphrase")
        print("\nOption 2: Create kucoin_keys.txt file:")
        print("  API_KEY=your_key")
        print("  API_SECRET=your_secret")
        print("  API_PASSPHRASE=your_passphrase")
        print("\nOption 3: Create .env file (see .env.example)")
        return None, None, None
    except Exception as e:
        logger.error(f"Error loading KuCoin credentials: {e}", exc_info=True)
        print(f"❌ Error loading credentials: {e}")
        return None, None, None


if __name__ == "__main__":
    # Demo
    api_key, api_secret, api_passphrase = load_kucoin_credentials()
    
    if api_key:
        client = KuCoinAPI(api_key, api_secret, api_passphrase)
        
        print("\n📊 Getting account balance...")
        balance = client.get_account_balance()
        print(f"Balance: {balance}")
    else:
        print("Please set up your KuCoin API credentials first.")
