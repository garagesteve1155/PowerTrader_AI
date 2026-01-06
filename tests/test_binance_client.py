import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlencode, urlparse

import responses

from exchanges.binance_client import BinanceExchangeClient


BASE_URL = "https://api.binance.com"


@responses.activate
def test_get_price_public():
    client = BinanceExchangeClient(
        api_key="key",
        api_secret="secret",
        base_url=BASE_URL,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/v3/ticker/price?symbol=BTCUSDT",
        json={"symbol": "BTCUSDT", "price": "50000.00"},
        status=200,
    )
    price = client.get_price("BTC")
    assert price == 50000.0


@responses.activate
def test_get_klines():
    client = BinanceExchangeClient(
        api_key="key",
        api_secret="secret",
        base_url=BASE_URL,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2",
        json=[
            [1700000000000, "1.0", "2.0", "0.5", "1.5", "100", 0, 0, 0, 0, 0, 0],
            [1700000060000, "1.5", "2.5", "1.0", "2.0", "120", 0, 0, 0, 0, 0, 0],
        ],
        status=200,
    )
    candles = client.get_klines("BTC", "1m", 2)
    assert candles == [
        {"ts": 1700000000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100.0},
        {"ts": 1700000060, "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 120.0},
    ]


@responses.activate
def test_signed_request_signature_and_headers():
    client = BinanceExchangeClient(
        api_key="key",
        api_secret="secret",
        base_url=BASE_URL,
        recv_window=5000,
    )
    client._timestamp_ms = lambda: 1700000000000
    client._last_time_sync = time.time()

    def account_callback(request):
        parsed = urlparse(request.url)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        signature = qs.pop("signature")
        expected_query = urlencode(sorted(qs.items()), doseq=True)
        expected_sig = hmac.new(
            b"secret",
            expected_query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert signature == expected_sig
        assert request.headers.get("X-MBX-APIKEY") == "key"
        assert qs["recvWindow"] == "5000"
        assert qs["timestamp"] == "1700000000000"

        body = {"balances": [{"asset": "USDT", "free": "12.3", "locked": "0"}]}
        return (200, {}, json.dumps(body))

    responses.add_callback(
        responses.GET,
        f"{BASE_URL}/api/v3/account",
        callback=account_callback,
        content_type="application/json",
    )

    balances = client.get_balances()
    assert balances["USDT"]["total"] == 12.3


@responses.activate
def test_create_order_market_and_limit():
    client = BinanceExchangeClient(
        api_key="key",
        api_secret="secret",
        base_url=BASE_URL,
    )
    client._timestamp_ms = lambda: 1700000000000
    client._last_time_sync = time.time()

    exchange_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1", "minPrice": "0.1"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
                ],
            }
        ]
    }

    responses.add(
        responses.GET,
        f"{BASE_URL}/api/v3/exchangeInfo?symbol=BTCUSDT",
        json=exchange_info,
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/v3/ticker/price?symbol=BTCUSDT",
        json={"symbol": "BTCUSDT", "price": "50000.0"},
        status=200,
    )

    order_queries = []

    def order_callback(request):
        parsed = urlparse(request.url)
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        order_queries.append(qs)
        body = {"orderId": 123, "symbol": qs.get("symbol", "")}
        return (200, {}, json.dumps(body))

    responses.add_callback(
        responses.POST,
        f"{BASE_URL}/api/v3/order",
        callback=order_callback,
        content_type="application/json",
    )
    responses.add_callback(
        responses.POST,
        f"{BASE_URL}/api/v3/order",
        callback=order_callback,
        content_type="application/json",
    )

    market_order = client.create_order("BTC", side="BUY", type="MARKET", quantity=0.001)
    limit_order = client.create_order("BTC", side="SELL", type="LIMIT", quantity=0.00123, price=12345.67)

    assert market_order["orderId"] == 123
    assert limit_order["orderId"] == 123
    assert order_queries[0]["type"] == "MARKET"
    assert order_queries[0]["side"] == "BUY"
    assert order_queries[0]["symbol"] == "BTCUSDT"
    assert order_queries[0]["quantity"] == "0.001"
    assert "signature" in order_queries[0]

    assert order_queries[1]["type"] == "LIMIT"
    assert order_queries[1]["side"] == "SELL"
    assert order_queries[1]["symbol"] == "BTCUSDT"
    assert order_queries[1]["quantity"] == "0.0012"
    assert order_queries[1]["price"] == "12345.6"
    assert order_queries[1]["timeInForce"] == "GTC"
