import hashlib
import hmac
import logging
import os
import random
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests


class BinanceAPIError(Exception):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        code: Optional[int] = None,
        endpoint: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.endpoint = endpoint
        self.params = params or {}


class BinanceRateLimitError(BinanceAPIError):
    pass


class BinanceTimestampError(BinanceAPIError):
    pass


class ExchangeClient:
    def get_price(self, symbol: str) -> float:
        raise NotImplementedError

    def get_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_balances(self) -> Dict[str, Dict[str, float]]:
        raise NotImplementedError

    def create_order(
        self,
        symbol: str,
        side: str,
        type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_order_history(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        raise NotImplementedError


class _RateLimiter:
    def __init__(self, max_per_second: Optional[float]) -> None:
        self._min_interval = 1.0 / float(max_per_second) if max_per_second else None
        self._last_ts = 0.0

    def wait(self) -> None:
        if not self._min_interval:
            return
        now = time.time()
        elapsed = now - self._last_ts
        remaining = self._min_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_ts = time.time()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, None)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _format_decimal(value: Decimal) -> str:
    s = format(value, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


class BinanceExchangeClient(ExchangeClient):
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        testnet: Optional[bool] = None,
        timeout: int = 10,
        recv_window: int = 5000,
        max_retries: int = 4,
        logger: Optional[logging.Logger] = None,
        default_quote: str = "USDT",
        rate_limit_per_sec: Optional[float] = None,
        time_sync_interval: int = 60,
        public_only: bool = False,
    ) -> None:
        self.public_only = bool(public_only)
        self.api_key = (api_key or os.environ.get("BINANCE_API_KEY", "")).strip()
        self.api_secret = (api_secret or os.environ.get("BINANCE_API_SECRET", "")).strip()
        if not self.public_only:
            if not self.api_key or not self.api_secret:
                raise ValueError(
                    "Missing BINANCE_API_KEY and/or BINANCE_API_SECRET in environment or constructor."
                )

        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            env_base_url = os.environ.get("BINANCE_API_BASE_URL", "").strip()
            if env_base_url:
                self.base_url = env_base_url.rstrip("/")
            else:
                use_testnet = bool(testnet) if testnet is not None else _env_flag("BINANCE_TESTNET", False)
                self.base_url = "https://testnet.binance.vision" if use_testnet else "https://api.binance.com"

        self.timeout = int(timeout)
        self.recv_window = int(recv_window)
        self.max_retries = int(max_retries)
        quote_env = os.environ.get("BINANCE_QUOTE_ASSET", None)
        self.default_quote = (quote_env or default_quote or "USDT").strip().upper()
        self.time_sync_interval = int(time_sync_interval)

        self.session = requests.Session()
        self.logger = logger or logging.getLogger("powertrader.exchange.binance")
        self._rate_limiter = _RateLimiter(rate_limit_per_sec)

        self._time_offset_ms = 0
        self._last_time_sync = 0.0

        # cache: symbol -> (ts, filters)
        self._exchange_info_cache: Dict[str, Tuple[float, Dict[str, Dict[str, str]]]] = {}

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _timestamp_ms(self) -> int:
        return int(self._now_ms() + self._time_offset_ms)

    def _sync_time(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_time_sync) < self.time_sync_interval:
            return
        data = self._request("GET", "/api/v3/time", signed=False, skip_time_sync=True)
        server_time = int(data.get("serverTime", 0))
        if server_time > 0:
            local_time = self._now_ms()
            self._time_offset_ms = server_time - local_time
            self._last_time_sync = now

    def _normalize_symbol(self, symbol: str) -> str:
        raw = (symbol or "").strip().upper()
        if not raw:
            raise ValueError("Symbol is required.")

        if "-" in raw:
            base, quote = raw.split("-", 1)
        elif "_" in raw:
            base, quote = raw.split("_", 1)
        elif "/" in raw:
            base, quote = raw.split("/", 1)
        else:
            base, quote = raw, ""
            known_quotes = [self.default_quote, "USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "USD"]
            for q in known_quotes:
                if raw.endswith(q) and len(raw) > len(q):
                    base = raw[: -len(q)]
                    quote = q
                    break

        base = (base or "").strip().upper()
        quote = (quote or "").strip().upper()
        if not base:
            raise ValueError(f"Invalid symbol format: {symbol}")
        if not quote:
            quote = self.default_quote
        if quote == "USD":
            quote = "USDT"
        return f"{base}{quote}"

    def _sign_params(self, params: Dict[str, Any]) -> str:
        query = urlencode(sorted(params.items()), doseq=True)
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _redacted_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        redacted = dict(params)
        if "signature" in redacted:
            redacted["signature"] = "***"
        return redacted

    def _parse_error(self, resp: requests.Response) -> Tuple[Optional[int], str, Dict[str, Any]]:
        try:
            data = resp.json() or {}
        except Exception:
            data = {}
        code = data.get("code")
        msg = data.get("msg") or resp.text or "Binance API error"
        return code, msg, data

    def _sleep_backoff(self, attempt: int, base: float = 0.5, factor: float = 2.0, cap: float = 10.0) -> None:
        delay = min(cap, base * (factor ** attempt))
        jitter = random.uniform(0.0, delay * 0.1)
        time.sleep(delay + jitter)

    def _sleep_rate_limit(self, resp: requests.Response, attempt: int) -> None:
        retry_after = resp.headers.get("Retry-After", None)
        if retry_after:
            try:
                time.sleep(float(retry_after))
                return
            except Exception:
                pass
        self._sleep_backoff(attempt)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        skip_time_sync: bool = False,
    ) -> Any:
        method = method.upper().strip()
        url = f"{self.base_url}{path}"
        params = params or {}

        if signed and self.public_only:
            raise BinanceAPIError(
                "Signed endpoints require BINANCE_API_KEY/BINANCE_API_SECRET.",
                endpoint=path,
                params=self._redacted_params(params),
            )

        resynced = False
        for attempt in range(self.max_retries + 1):
            if signed and not skip_time_sync:
                self._sync_time()

            req_params = dict(params)
            headers: Dict[str, str] = {}
            if signed:
                req_params["timestamp"] = self._timestamp_ms()
                req_params["recvWindow"] = self.recv_window
                req_params["signature"] = self._sign_params(req_params)
                headers["X-MBX-APIKEY"] = self.api_key

            try:
                self._rate_limiter.wait()
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=req_params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                self.logger.warning(
                    "Binance request error: %s %s error=%s params=%s",
                    method,
                    path,
                    exc,
                    self._redacted_params(req_params),
                )
                raise BinanceAPIError(str(exc), endpoint=path, params=self._redacted_params(req_params))

            if resp.status_code in (418, 429):
                if attempt < self.max_retries:
                    self._sleep_rate_limit(resp, attempt)
                    continue
                self.logger.warning(
                    "Binance rate limit: %s %s status=%s params=%s",
                    method,
                    path,
                    resp.status_code,
                    self._redacted_params(req_params),
                )
                raise BinanceRateLimitError(
                    f"Rate limited ({resp.status_code}).",
                    status_code=resp.status_code,
                    endpoint=path,
                    params=self._redacted_params(req_params),
                )

            if resp.status_code >= 400:
                code, msg, data = self._parse_error(resp)
                if code in (-1021, -1022):
                    if not resynced:
                        self._sync_time(force=True)
                        resynced = True
                        continue
                    self.logger.warning(
                        "Binance timestamp/signature error: %s %s status=%s code=%s msg=%s params=%s",
                        method,
                        path,
                        resp.status_code,
                        code,
                        msg,
                        self._redacted_params(req_params),
                    )
                    raise BinanceTimestampError(
                        msg,
                        status_code=resp.status_code,
                        code=code,
                        endpoint=path,
                        params=self._redacted_params(req_params),
                    )
                self.logger.warning(
                    "Binance API error: %s %s status=%s code=%s msg=%s params=%s",
                    method,
                    path,
                    resp.status_code,
                    code,
                    msg,
                    self._redacted_params(req_params),
                )
                raise BinanceAPIError(
                    msg,
                    status_code=resp.status_code,
                    code=code,
                    endpoint=path,
                    params=self._redacted_params(req_params),
                )

            try:
                return resp.json()
            except Exception:
                return resp.text

        raise BinanceAPIError("Unexpected request failure.", endpoint=path, params=self._redacted_params(params))

    def get_price(self, symbol: str) -> float:
        symbol_norm = self._normalize_symbol(symbol)
        data = self._request("GET", "/api/v3/ticker/price", params={"symbol": symbol_norm}, signed=False)
        return float(data.get("price", 0.0))

    def get_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        symbol_norm = self._normalize_symbol(symbol)
        data = self._request(
            "GET",
            "/api/v3/klines",
            params={"symbol": symbol_norm, "interval": interval, "limit": int(limit)},
            signed=False,
        )
        candles: List[Dict[str, Any]] = []
        for row in data or []:
            try:
                ts_ms = int(row[0])
                o = float(row[1])
                h = float(row[2])
                l = float(row[3])
                c = float(row[4])
                v = float(row[5]) if len(row) > 5 else 0.0
                candles.append(
                    {
                        "ts": int(ts_ms / 1000),
                        "open": o,
                        "high": h,
                        "low": l,
                        "close": c,
                        "volume": v,
                    }
                )
            except Exception:
                continue
        return candles

    def get_balances(self) -> Dict[str, Dict[str, float]]:
        data = self._request("GET", "/api/v3/account", signed=True)
        balances: Dict[str, Dict[str, float]] = {}
        for bal in data.get("balances", []):
            asset = str(bal.get("asset", "")).strip().upper()
            if not asset:
                continue
            try:
                free = float(bal.get("free", 0.0))
                locked = float(bal.get("locked", 0.0))
            except Exception:
                free = 0.0
                locked = 0.0
            balances[asset] = {"free": free, "locked": locked, "total": free + locked}
        return balances

    def get_exchange_info(self, symbol: str) -> Dict[str, Dict[str, str]]:
        symbol_norm = self._normalize_symbol(symbol)
        cached = self._exchange_info_cache.get(symbol_norm)
        now = time.time()
        if cached and (now - cached[0]) < 900:
            return dict(cached[1])

        data = self._request(
            "GET",
            "/api/v3/exchangeInfo",
            params={"symbol": symbol_norm},
            signed=False,
        )
        symbols = data.get("symbols") or []
        if not symbols:
            raise BinanceAPIError(f"No exchangeInfo for symbol {symbol_norm}.", endpoint="/api/v3/exchangeInfo")
        filters = symbols[0].get("filters") or []

        out: Dict[str, Dict[str, str]] = {}
        for f in filters:
            ftype = f.get("filterType")
            if ftype:
                out[str(ftype)] = {k: str(v) for k, v in f.items()}

        self._exchange_info_cache[symbol_norm] = (now, out)
        return dict(out)

    def adjust_order_params(
        self,
        symbol: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> Tuple[float, Optional[float], bool]:
        symbol_norm = self._normalize_symbol(symbol)
        filters = self.get_exchange_info(symbol_norm)
        changed = False

        qty = Decimal(str(quantity))
        px = Decimal(str(price)) if price is not None else None

        lot = filters.get("LOT_SIZE", {})
        step_size = Decimal(lot.get("stepSize", "0")) if lot else Decimal("0")
        min_qty = Decimal(lot.get("minQty", "0")) if lot else Decimal("0")

        if step_size and step_size > 0:
            new_qty = (qty / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size
            if new_qty != qty:
                qty = new_qty
                changed = True

        if min_qty and qty < min_qty:
            raise ValueError(f"Quantity {qty} is below minQty {min_qty} for {symbol_norm}.")

        price_filter = filters.get("PRICE_FILTER", {})
        tick_size = Decimal(price_filter.get("tickSize", "0")) if price_filter else Decimal("0")
        min_price = Decimal(price_filter.get("minPrice", "0")) if price_filter else Decimal("0")

        if px is not None and tick_size and tick_size > 0:
            new_px = (px / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size
            if new_px != px:
                px = new_px
                changed = True
            if min_price and px < min_price:
                raise ValueError(f"Price {px} is below minPrice {min_price} for {symbol_norm}.")

        min_notional = filters.get("MIN_NOTIONAL", {}).get("minNotional", None)
        if min_notional:
            notional_threshold = Decimal(str(min_notional))
            if px is None:
                px = Decimal(str(self.get_price(symbol_norm)))
            notional = qty * px
            if notional < notional_threshold:
                raise ValueError(
                    f"Order notional {notional} below minNotional {notional_threshold} for {symbol_norm}."
                )

        return float(qty), float(px) if px is not None else None, changed

    def create_order(
        self,
        symbol: str,
        side: str,
        type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        symbol_norm = self._normalize_symbol(symbol)
        side = side.upper().strip()
        order_type = type.upper().strip()

        adj_qty, adj_price, _ = self.adjust_order_params(symbol_norm, quantity, price)
        params: Dict[str, Any] = {
            "symbol": symbol_norm,
            "side": side,
            "type": order_type,
            "quantity": _format_decimal(Decimal(str(adj_qty))),
        }

        if order_type == "LIMIT":
            if adj_price is None:
                raise ValueError("LIMIT orders require price.")
            params["price"] = _format_decimal(Decimal(str(adj_price)))
            params["timeInForce"] = "GTC"

        return self._request("POST", "/api/v3/order", params=params, signed=True)

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        symbol_norm = self._normalize_symbol(symbol)
        return self._request(
            "DELETE",
            "/api/v3/order",
            params={"symbol": symbol_norm, "orderId": order_id},
            signed=True,
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = self._normalize_symbol(symbol)
        return self._request("GET", "/api/v3/openOrders", params=params, signed=True)

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        symbol_norm = self._normalize_symbol(symbol)
        return self._request(
            "GET",
            "/api/v3/order",
            params={"symbol": symbol_norm, "orderId": order_id},
            signed=True,
        )

    def get_order_history(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        symbol_norm = self._normalize_symbol(symbol)
        return self._request(
            "GET",
            "/api/v3/allOrders",
            params={"symbol": symbol_norm, "limit": int(limit)},
            signed=True,
        )


class BinancePaperExchangeClient(ExchangeClient):
    def __init__(
        self,
        base_url: Optional[str] = None,
        testnet: Optional[bool] = None,
        timeout: int = 10,
        recv_window: int = 5000,
        max_retries: int = 4,
        logger: Optional[logging.Logger] = None,
        default_quote: str = "USDT",
        rate_limit_per_sec: Optional[float] = None,
        time_sync_interval: int = 60,
        starting_balance: Optional[float] = None,
    ) -> None:
        self.logger = logger or logging.getLogger("powertrader.exchange.binance.paper")
        self._public = BinanceExchangeClient(
            api_key="",
            api_secret="",
            base_url=base_url,
            testnet=testnet,
            timeout=timeout,
            recv_window=recv_window,
            max_retries=max_retries,
            logger=logger,
            default_quote=default_quote,
            rate_limit_per_sec=rate_limit_per_sec,
            time_sync_interval=time_sync_interval,
            public_only=True,
        )
        self.default_quote = self._public.default_quote

        env_balance = os.environ.get("BINANCE_PAPER_BALANCE", None)
        if starting_balance is None:
            starting_balance = float(env_balance) if env_balance else 1000.0

        self._balances: Dict[str, Dict[str, Decimal]] = {
            self.default_quote: {"free": Decimal(str(starting_balance)), "locked": Decimal("0")}
        }
        self._orders: List[Dict[str, Any]] = []
        self._next_order_id = 1
        fee_rate_env = os.environ.get("BINANCE_PAPER_FEE_RATE", "").strip()
        self.taker_fee_rate = float(os.environ.get("BINANCE_TAKER_FEE_RATE", fee_rate_env or "0.001"))
        self.maker_fee_rate = float(os.environ.get("BINANCE_MAKER_FEE_RATE", fee_rate_env or "0.001"))
        self.slippage_pct = float(os.environ.get("BINANCE_PAPER_SLIPPAGE_PCT", "0.0") or 0.0)
        self.partial_fill_enabled = _env_flag("BINANCE_PAPER_PARTIAL_FILL", False)
        self.partial_fill_min = float(os.environ.get("BINANCE_PAPER_PARTIAL_FILL_MIN", "0.6") or 0.6)
        self.partial_fill_max = float(os.environ.get("BINANCE_PAPER_PARTIAL_FILL_MAX", "1.0") or 1.0)

    def _split_symbol(self, symbol_norm: str) -> Tuple[str, str]:
        known_quotes = [self.default_quote, "USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "USD"]
        for quote in known_quotes:
            if symbol_norm.endswith(quote) and len(symbol_norm) > len(quote):
                return symbol_norm[: -len(quote)], quote
        return symbol_norm[: -len(self.default_quote)], self.default_quote

    def _get_balance(self, asset: str) -> Dict[str, Decimal]:
        asset = asset.strip().upper()
        if asset not in self._balances:
            self._balances[asset] = {"free": Decimal("0"), "locked": Decimal("0")}
        return self._balances[asset]

    def _record_order(self, order: Dict[str, Any]) -> None:
        self._orders.append(order)

    def _new_order_id(self) -> int:
        oid = self._next_order_id
        self._next_order_id += 1
        return oid

    def get_price(self, symbol: str) -> float:
        return self._public.get_price(symbol)

    def get_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        return self._public.get_klines(symbol, interval, limit)

    def get_balances(self) -> Dict[str, Dict[str, float]]:
        out: Dict[str, Dict[str, float]] = {}
        for asset, bal in self._balances.items():
            free = float(bal.get("free", Decimal("0")))
            locked = float(bal.get("locked", Decimal("0")))
            out[asset] = {"free": free, "locked": locked, "total": free + locked}
        return out

    def create_order(
        self,
        symbol: str,
        side: str,
        type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        symbol_norm = self._public._normalize_symbol(symbol)
        side = side.upper().strip()
        order_type = type.upper().strip()

        adj_qty, adj_price, _ = self._public.adjust_order_params(symbol_norm, quantity, price)
        qty = Decimal(str(adj_qty))

        fill_price = Decimal(str(adj_price)) if adj_price is not None else Decimal(str(self.get_price(symbol_norm)))
        if self.slippage_pct > 0:
            slip = Decimal(str(random.uniform(0.0, float(self.slippage_pct))))
            if side == "BUY":
                fill_price *= (Decimal("1") + slip)
            else:
                fill_price *= (Decimal("1") - slip)

        filled_qty = qty
        if self.partial_fill_enabled:
            pf_min = max(0.01, min(self.partial_fill_min, self.partial_fill_max))
            pf_max = max(pf_min, self.partial_fill_max)
            filled_qty = qty * Decimal(str(random.uniform(pf_min, pf_max)))
            filled_qty = max(Decimal("0"), min(qty, filled_qty))

        notional = filled_qty * fill_price

        base, quote = self._split_symbol(symbol_norm)
        fee_rate = Decimal(str(self.taker_fee_rate if order_type == "MARKET" else self.maker_fee_rate))
        fee = Decimal("0")
        fee_asset = ""
        executed_qty = filled_qty

        if side == "BUY":
            quote_bal = self._get_balance(quote)
            if quote_bal["free"] < notional:
                raise ValueError(f"Insufficient {quote} balance for paper buy.")
            quote_bal["free"] -= notional
            base_bal = self._get_balance(base)
            fee = (filled_qty * fee_rate)
            executed_qty = filled_qty - fee
            if executed_qty < 0:
                executed_qty = Decimal("0")
            base_bal["free"] += executed_qty
            fee_asset = base
        elif side == "SELL":
            base_bal = self._get_balance(base)
            if base_bal["free"] < filled_qty:
                raise ValueError(f"Insufficient {base} balance for paper sell.")
            base_bal["free"] -= filled_qty
            quote_bal = self._get_balance(quote)
            fee = (notional * fee_rate)
            quote_bal["free"] += (notional - fee)
            fee_asset = quote
        else:
            raise ValueError("Order side must be BUY or SELL.")

        order_id = self._new_order_id()
        order = {
            "orderId": order_id,
            "symbol": symbol_norm,
            "side": side,
            "type": order_type,
            "status": "FILLED" if filled_qty == qty else "PARTIALLY_FILLED",
            "price": _format_decimal(fill_price),
            "origQty": _format_decimal(qty),
            "executedQty": _format_decimal(executed_qty),
            "cummulativeQuoteQty": _format_decimal(notional),
            "fee": _format_decimal(fee),
            "feeAsset": fee_asset,
            "time": int(time.time() * 1000),
        }
        self._record_order(order)
        return order

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        symbol_norm = self._public._normalize_symbol(symbol)
        return {"orderId": order_id, "symbol": symbol_norm, "status": "CANCELED"}

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return []

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        for order in self._orders:
            if str(order.get("orderId")) == str(order_id):
                return dict(order)
        symbol_norm = self._public._normalize_symbol(symbol)
        return {"orderId": order_id, "symbol": symbol_norm, "status": "UNKNOWN"}

    def get_order_history(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        symbol_norm = self._public._normalize_symbol(symbol)
        out = [o for o in self._orders if o.get("symbol") == symbol_norm]
        return out[-int(limit) :] if limit else out
