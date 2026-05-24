"""
Robinhood exchange adapter.

Extracted from pt_trader.py — handles all Robinhood Crypto Trading API
communication: auth, signing, order placement, fill extraction, polling.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

import requests
from nacl.signing import SigningKey

from exchange_api import Exchange, OrderResult


class RobinhoodExchange(Exchange):

    @property
    def key(self) -> str:
        return "robinhood"

    @property
    def display_name(self) -> str:
        return "Robinhood"


    def __init__(self, api_key: str, base64_private_key: str,
                 base_url: str = "https://trading.robinhood.com",
                 debug_dir: Optional[str] = None):
        self.api_key = (api_key or "").strip()
        raw_key = base64.b64decode((base64_private_key or "").strip())
        self.private_key = SigningKey(raw_key)
        self.base_url = base_url
        self._debug_dir = debug_dir
        self._last_good_bid_ask: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Low-level API
    # ------------------------------------------------------------------

    def _get_current_timestamp(self) -> int:
        return int(time.time())

    def _get_authorization_header(self, method: str, path: str, body: str, timestamp: int) -> dict:
        method = method.upper()
        body = body or ""
        message = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self.private_key.sign(message.encode("utf-8"))
        return {
            "x-api-key": self.api_key,
            "x-signature": base64.b64encode(signed.signature).decode("utf-8"),
            "x-timestamp": str(timestamp),
            "Content-Type": "application/json",
        }

    def make_api_request(self, method: str, path: str, body: str = "") -> Any:
        url = self.base_url + path
        ts = self._get_current_timestamp()
        headers = self._get_authorization_header(method, path, body, ts)
        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, timeout=10)
            elif method.upper() == "POST":
                resp = requests.post(url, headers=headers, json=json.loads(body), timeout=10)
            else:
                resp = requests.request(method.upper(), url, headers=headers, data=body or None, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError:
            try:
                return resp.json()
            except Exception:
                return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # ExchangeAdapter interface
    # ------------------------------------------------------------------

    def to_exchange_symbol(self, canonical: str) -> str:
        return canonical.replace("_", "-")

    def to_canonical_symbol(self, exchange_sym: str) -> str:
        return exchange_sym.replace("-", "_")

    def get_account_value(self) -> Optional[float]:
        try:
            acct = self.make_api_request("GET", "/api/v1/crypto/trading/accounts/")
            if isinstance(acct, dict):
                bp = float(acct.get("buying_power", 0) or 0)
                holdings = self._get_holdings_raw()
                hval = 0.0
                for h in (holdings.get("results", []) or []):
                    try:
                        qty = float(h.get("total_quantity", 0) or 0)
                        code = str(h.get("asset_code", "")).upper()
                        if code == "USDC":
                            continue
                        sym = f"{code}-USD"
                        path = f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={sym}"
                        data = self.make_api_request("GET", path)
                        if data and "results" in data and data["results"]:
                            bid = float(data["results"][0].get("bid_inclusive_of_sell_spread", 0) or 0)
                            hval += qty * bid
                    except Exception:
                        continue
                return bp + hval
        except Exception:
            pass
        return None

    def get_buying_power(self) -> Optional[float]:
        try:
            acct = self.make_api_request("GET", "/api/v1/crypto/trading/accounts/")
            if isinstance(acct, dict):
                return float(acct.get("buying_power", 0) or 0)
        except Exception:
            pass
        return None

    def get_holdings(self) -> Dict[str, float]:
        raw = self._get_holdings_raw()
        out: Dict[str, float] = {}
        for h in (raw.get("results", []) or []):
            try:
                code = str(h.get("asset_code", "")).upper().strip()
                if not code or code == "USDC":
                    continue
                qty = float(h.get("total_quantity", 0) or 0)
                out[code] = qty
            except Exception:
                continue
        return out

    def _get_holdings_raw(self) -> dict:
        resp = self.make_api_request("GET", "/api/v1/crypto/trading/holdings/")
        return resp if isinstance(resp, dict) else {"results": []}

    def get_price(self, symbols: List[str]) -> Tuple[Dict[str, float], Dict[str, float], List[str]]:
        buy_prices: Dict[str, float] = {}
        sell_prices: Dict[str, float] = {}
        valid: List[str] = []

        for canonical in symbols:
            base = self.base_from_canonical(canonical)
            if base == "USDC":
                continue
            rh_sym = self.to_exchange_symbol(canonical)
            path = f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={rh_sym}"
            resp = self.make_api_request("GET", path)

            if resp and "results" in resp and resp["results"]:
                result = resp["results"][0]
                ask = float(result["ask_inclusive_of_buy_spread"])
                bid = float(result["bid_inclusive_of_sell_spread"])
                buy_prices[canonical] = ask
                sell_prices[canonical] = bid
                valid.append(canonical)
                self._last_good_bid_ask[canonical] = {"ask": ask, "bid": bid, "ts": time.time()}
            else:
                cached = self._last_good_bid_ask.get(canonical)
                if cached:
                    ask = float(cached.get("ask", 0) or 0)
                    bid = float(cached.get("bid", 0) or 0)
                    if ask > 0 and bid > 0:
                        buy_prices[canonical] = ask
                        sell_prices[canonical] = bid
                        valid.append(canonical)

        return buy_prices, sell_prices, valid

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_buy(self, symbol: str, amount_usd: float, tag: Optional[str] = None) -> Optional[OrderResult]:
        rh_sym = self.to_exchange_symbol(symbol)

        buy_prices, _, _ = self.get_price([symbol])
        if symbol not in buy_prices:
            return None
        current_price = buy_prices[symbol]
        asset_quantity = amount_usd / current_price

        max_retries = 5
        for _ in range(max_retries):
            response = None
            try:
                rounded_quantity = round(asset_quantity, 8)
                body = {
                    "client_order_id": str(uuid.uuid4()),
                    "side": "buy",
                    "type": "market",
                    "symbol": rh_sym,
                    "market_order_config": {
                        "asset_quantity": f"{rounded_quantity:.8f}"
                    }
                }
                response = self.make_api_request("POST", "/api/v1/crypto/trading/orders/", json.dumps(body))

                if response and "errors" not in response:
                    order_id = response.get("id")
                    if not order_id:
                        return None

                    order = self._wait_for_order_terminal(rh_sym, order_id)
                    if not order:
                        return None

                    state = str(order.get("state", "")).lower().strip()
                    if state != "filled":
                        return OrderResult(order_id=order_id, state=state, raw=order)

                    self._write_debug_dump(rh_sym, "BUY", order_id, order)
                    qty, price, notional, fees = self._extract_amounts_and_fees(order)
                    return OrderResult(
                        order_id=order_id, state="filled",
                        filled_qty=qty, avg_price=price,
                        notional_usd=notional, fees_usd=fees, raw=order,
                    )
            except Exception:
                pass

            if response and "errors" in response:
                adjusted = self._adjust_precision(response, asset_quantity)
                if adjusted is None:
                    return None
                asset_quantity = adjusted

        return None

    def place_sell(self, symbol: str, qty: float) -> Optional[OrderResult]:
        rh_sym = self.to_exchange_symbol(symbol)
        body = {
            "client_order_id": str(uuid.uuid4()),
            "side": "sell",
            "type": "market",
            "symbol": rh_sym,
            "market_order_config": {
                "asset_quantity": f"{qty:.8f}"
            }
        }
        response = self.make_api_request("POST", "/api/v1/crypto/trading/orders/", json.dumps(body))

        if not response or not isinstance(response, dict) or "errors" in response:
            return None

        order_id = response.get("id")
        if not order_id:
            return None

        order = self._wait_for_order_terminal(rh_sym, order_id)
        if not order:
            return OrderResult(order_id=order_id, state="unknown", raw=response)

        state = str(order.get("state", "")).lower().strip()
        if state != "filled":
            return OrderResult(order_id=order_id, state=state, raw=order)

        self._write_debug_dump(rh_sym, "SELL", order_id, order)
        filled_qty, avg_price, notional, fees = self._extract_amounts_and_fees(order)
        return OrderResult(
            order_id=order_id, state="filled",
            filled_qty=filled_qty, avg_price=avg_price,
            notional_usd=notional, fees_usd=fees, raw=order,
        )

    def get_orders(self, symbol: str) -> dict:
        rh_sym = self.to_exchange_symbol(symbol)
        path = f"/api/v1/crypto/trading/orders/?symbol={rh_sym}"
        first = self.make_api_request("GET", path)
        if not isinstance(first, dict):
            return first or {}

        results = list(first.get("results", []) or [])
        next_url = first.get("next")
        pages = 1
        max_pages = 25

        while next_url and pages < max_pages:
            try:
                nxt = str(next_url).strip()
                if not nxt:
                    break
                if nxt.startswith(self.base_url):
                    nxt_path = nxt[len(self.base_url):]
                elif nxt.startswith("/"):
                    nxt_path = nxt
                elif "://" in nxt:
                    try:
                        nxt_path = "/" + nxt.split("://", 1)[1].split("/", 1)[1]
                    except Exception:
                        break
                else:
                    nxt_path = "/" + nxt

                resp = self.make_api_request("GET", nxt_path)
                if not isinstance(resp, dict):
                    break
                results.extend(list(resp.get("results", []) or []))
                next_url = resp.get("next")
                pages += 1
            except Exception:
                break

        out = dict(first)
        out["results"] = results
        out["next"] = None
        return out

    def get_order_result(self, symbol: str, order_id: str) -> Optional[OrderResult]:
        rh_sym = self.to_exchange_symbol(symbol)
        order = self._wait_for_order_terminal(rh_sym, order_id)
        if not order:
            return None
        state = str(order.get("state", "")).lower().strip()
        if state != "filled":
            return OrderResult(order_id=order_id, state=state, raw=order)
        qty, price, notional, fees = self._extract_amounts_and_fees(order)
        return OrderResult(
            order_id=order_id, state="filled",
            filled_qty=qty, avg_price=price,
            notional_usd=notional, fees_usd=fees, raw=order,
        )

    def has_valid_trading_pairs(self) -> bool:
        resp = self.make_api_request("GET", "/api/v1/crypto/trading/trading_pairs/")
        if not resp or "results" not in resp:
            return False
        return bool(resp.get("results"))

    # ------------------------------------------------------------------
    # Cost basis from Robinhood order history
    # ------------------------------------------------------------------

    def calculate_cost_basis_from_orders(
        self,
        bot_order_ids: Dict[str, set],
        bot_order_ids_from_history: Dict[str, set],
        pnl_ledger: dict,
    ) -> Dict[str, float]:
        holdings = self._get_holdings_raw()
        if not holdings or "results" not in holdings:
            return {}

        cost_basis: Dict[str, float] = {}

        for holding in (holdings.get("results", []) or []):
            asset_code = str(holding.get("asset_code", "")).upper().strip()
            if not asset_code:
                continue
            try:
                total_qty = float(holding.get("total_quantity", 0) or 0)
            except Exception:
                total_qty = 0.0

            try:
                pos = (pnl_ledger.get("open_positions", {}) or {}).get(asset_code)
                tradable_target_qty = float(pos.get("qty", 0) or 0) if isinstance(pos, dict) else 0.0
            except Exception:
                tradable_target_qty = 0.0

            if tradable_target_qty <= 1e-12:
                cost_basis[asset_code] = 0.0
                continue

            orders = self.get_orders(f"{asset_code}_USD")
            if not orders or "results" not in orders:
                continue

            all_ids = set()
            for src in (bot_order_ids, bot_order_ids_from_history):
                if isinstance(src, dict):
                    ids = src.get(asset_code, set())
                    if isinstance(ids, (set, frozenset)):
                        all_ids.update(ids)
                    elif isinstance(ids, list):
                        all_ids.update(ids)

            filled_bot_buys = []
            for o in (orders.get("results", []) or []):
                try:
                    if o.get("state") != "filled":
                        continue
                    if str(o.get("side", "")).lower().strip() != "buy":
                        continue
                    oid = str(o.get("id", "")).strip()
                    if oid and oid in all_ids:
                        filled_bot_buys.append(o)
                except Exception:
                    continue

            if not filled_bot_buys:
                continue

            filled_bot_buys.sort(key=lambda x: x.get("created_at", ""))

            lots = []
            for o in filled_bot_buys:
                try:
                    q, p = self._extract_fill(o)
                    if q > 0 and p is not None and float(p) > 0:
                        lots.append((float(q), float(p)))
                except Exception:
                    continue

            bot_qty = sum(q for (q, _) in lots)
            if bot_qty <= 1e-12:
                cost_basis[asset_code] = 0.0
                continue

            target_qty = min(float(bot_qty), float(tradable_target_qty))
            remaining = float(target_qty)
            total_cost = 0.0
            for q, p in lots:
                if remaining <= 0:
                    break
                use_q = min(float(q), float(remaining))
                total_cost += float(use_q) * float(p)
                remaining -= float(use_q)

            cost_basis[asset_code] = float(total_cost) / float(target_qty) if target_qty > 1e-12 else 0.0

        return cost_basis

    # ------------------------------------------------------------------
    # Fill extraction (Robinhood-specific response parsing)
    # ------------------------------------------------------------------

    def _extract_fill(self, order: dict) -> Tuple[float, Optional[float]]:
        try:
            execs = order.get("executions", []) or []
            total_qty = 0.0
            total_notional = 0.0
            for ex in execs:
                try:
                    q = float(ex.get("quantity", 0) or 0)
                    p = float(ex.get("effective_price", 0) or 0)
                    if q > 0 and p > 0:
                        total_qty += q
                        total_notional += q * p
                except Exception:
                    continue

            avg_price = (total_notional / total_qty) if (total_qty > 0 and total_notional > 0) else None

            if total_qty <= 0:
                for k in ("filled_asset_quantity", "filled_quantity", "asset_quantity", "quantity"):
                    if k in order:
                        try:
                            v = float(order.get(k) or 0)
                            if v > 0:
                                total_qty = v
                                break
                        except Exception:
                            continue

            if avg_price is None:
                for k in ("average_price", "avg_price", "price", "effective_price"):
                    if k in order:
                        try:
                            v = float(order.get(k) or 0)
                            if v > 0:
                                avg_price = v
                                break
                        except Exception:
                            continue

            return float(total_qty), (float(avg_price) if avg_price is not None else None)
        except Exception:
            return 0.0, None

    def _extract_amounts_and_fees(self, order: dict) -> Tuple[float, Optional[float], Optional[float], Optional[float]]:
        def _fee_to_float(v) -> float:
            try:
                if v is None:
                    return 0.0
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    return float(v)
                if isinstance(v, list):
                    return sum(_fee_to_float(x) for x in v)
                if isinstance(v, dict):
                    for k in ("usd_amount", "amount", "value", "fee", "quantity"):
                        if k in v:
                            try:
                                return float(v[k])
                            except Exception:
                                continue
                return 0.0
            except Exception:
                return 0.0

        def _to_decimal(x) -> Decimal:
            try:
                return Decimal(str(x)) if x is not None else Decimal("0")
            except Exception:
                return Decimal("0")

        def _usd_cents(d: Decimal) -> Decimal:
            return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        try:
            execs = order.get("executions", []) or []
            fee_total = 0.0
            fee_found = False

            for ex in execs:
                try:
                    for fk in ("fee", "fees", "fee_amount", "fee_usd", "fee_in_usd"):
                        if fk in ex:
                            fee_found = True
                            fee_total += _fee_to_float(ex.get(fk))
                except Exception:
                    continue

            for fk in ("fee", "fees", "fee_amount", "fee_usd", "fee_in_usd"):
                if fk in order:
                    fee_found = True
                    fee_total += _fee_to_float(order.get(fk))

            fees_usd = float(fee_total) if fee_found else None

            avg_p_d = _to_decimal(order.get("average_price"))
            filled_q_d = _to_decimal(order.get("filled_asset_quantity"))

            avg_fill_price = float(avg_p_d) if avg_p_d > 0 else None
            filled_qty = float(filled_q_d) if filled_q_d > 0 else 0.0
            notional_usd = None

            if avg_p_d > 0 and filled_q_d > 0:
                notional_usd = float(_usd_cents(avg_p_d * filled_q_d))

            if notional_usd is None:
                total_notional_d = Decimal("0")
                total_qty_d = Decimal("0")
                for ex in execs:
                    try:
                        q_d = _to_decimal(ex.get("quantity"))
                        p_d = _to_decimal(ex.get("effective_price"))
                        if q_d > 0 and p_d > 0:
                            total_qty_d += q_d
                            total_notional_d += q_d * p_d
                    except Exception:
                        continue

                if total_qty_d > 0 and avg_fill_price is None:
                    try:
                        avg_fill_price = float(total_notional_d / total_qty_d)
                    except Exception:
                        pass

                if total_notional_d > 0:
                    notional_usd = float(_usd_cents(total_notional_d))

                if filled_qty <= 0 and total_qty_d > 0:
                    filled_qty = float(total_qty_d)

            return float(filled_qty), (float(avg_fill_price) if avg_fill_price is not None else None), notional_usd, fees_usd
        except Exception:
            return 0.0, None, None, None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _wait_for_order_terminal(self, rh_symbol: str, order_id: str) -> Optional[dict]:
        terminal = {"filled", "canceled", "cancelled", "rejected", "failed", "error"}
        while True:
            try:
                orders = self.make_api_request(
                    "GET", f"/api/v1/crypto/trading/orders/?symbol={rh_symbol}"
                )
                results = orders.get("results", []) if isinstance(orders, dict) else []
                for o in results:
                    if o.get("id") == order_id:
                        st = str(o.get("state", "")).lower().strip()
                        if st in terminal:
                            return o
                        break
            except Exception:
                pass
            time.sleep(1)

    def _adjust_precision(self, response: dict, asset_quantity: float) -> Optional[float]:
        for error in response.get("errors", []):
            detail = error.get("detail", "")
            if "has too much precision" in detail:
                try:
                    nearest_value = detail.split("nearest ")[1].split(" ")[0]
                    decimal_places = len(nearest_value.split(".")[1].rstrip("0"))
                    return round(asset_quantity, decimal_places)
                except Exception:
                    pass
            elif "must be greater than or equal to" in detail:
                return None
        return None

    def _write_debug_dump(self, rh_symbol: str, side: str, order_id: str, order: dict) -> None:
        if not self._debug_dir:
            return
        try:
            import datetime
            os.makedirs(self._debug_dir, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(self._debug_dir, f"{ts}_{rh_symbol}_{side}_{order_id}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(order, indent=2))
                f.write("\n\n")
                orders_resp = self.make_api_request(
                    "GET", f"/api/v1/crypto/trading/orders/?symbol={rh_symbol}"
                )
                f.write(json.dumps(orders_resp, indent=2))
                f.write("\n\n")
                f.write(json.dumps(order.get("executions", []), indent=2))
        except Exception:
            pass

    def replay_lots_fifo(self, filled_orders_oldest_first: list) -> list:
        lots = []

        def _buy_lots(order: dict) -> list:
            execs = order.get("executions", []) or []
            out = []
            for ex in execs:
                try:
                    q = float(ex.get("quantity", 0) or 0)
                    p = float(ex.get("effective_price", 0) or 0)
                    if q > 0 and p > 0:
                        out.append([q, p])
                except Exception:
                    continue
            if not out:
                q, p = self._extract_fill(order)
                if q > 0 and p is not None and float(p) > 0:
                    out.append([q, float(p)])
            return out

        def _sell_qty(order: dict) -> float:
            execs = order.get("executions", []) or []
            total = 0.0
            for ex in execs:
                try:
                    q = float(ex.get("quantity", 0) or 0)
                    p = float(ex.get("effective_price", 0) or 0)
                    if q > 0 and p > 0:
                        total += q
                except Exception:
                    continue
            if total <= 0:
                q, _ = self._extract_fill(order)
                total = float(q) if q > 0 else 0.0
            return total

        for order in filled_orders_oldest_first:
            side = str(order.get("side", "")).lower().strip()
            if side == "buy":
                for q, p in _buy_lots(order):
                    if q > 0 and p > 0:
                        lots.append([q, p])
            elif side == "sell":
                sq = _sell_qty(order)
                if sq <= 0:
                    continue
                remaining = sq
                while remaining > 0 and lots:
                    lq, lp = lots[0]
                    if lq > remaining:
                        lots[0][0] = lq - remaining
                        remaining = 0.0
                    else:
                        remaining -= lq
                        lots.pop(0)

        return [(float(q), float(p)) for q, p in lots if q > 0 and p > 0]


def create_exchange() -> RobinhoodExchange:
    from exchange_api import load_api_keys
    from pt_env import PTEnv

    keys = load_api_keys("robinhood")
    api_key = keys["api_key"]
    private_key = keys["api_secret"]  # base64-encoded nacl signing key

    if not api_key or not private_key:
        raise RuntimeError(
            "\n[PowerTrader] Robinhood API credentials not found.\n"
            "Fill in exchange_api_keys.json with your Robinhood api_key and api_secret.\n"
        )

    env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
    debug_dir = str(env.debug_trade_dumps_dir())

    return RobinhoodExchange(
        api_key=api_key,
        base64_private_key=private_key,
        debug_dir=debug_dir,
    )
