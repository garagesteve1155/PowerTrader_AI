from pt_exchange_api import buy, initialize_exchange


class _DummyExchange:
    def __init__(self):
        self.calls = []

    def place_market_order(self, symbol, side, quantity):
        self.calls.append((symbol, side, quantity))
        return {
            "status": "success",
            "orderId": "abc123",
            "price": 10.0,
            "total": quantity * 10.0,
            "message": "ok",
        }


def test_buy_treats_success_status_as_success(monkeypatch):
    dummy = _DummyExchange()
    monkeypatch.setattr("pt_exchange_api._exchange", dummy, raising=False)
    monkeypatch.setattr("pt_exchange_api._exchange_mode", "SIMULATOR", raising=False)

    result = buy("BTC", 1.5)

    assert result["success"] is True
    assert dummy.calls == [("BTC-USDT", "buy", 1.5)]


def test_initialize_exchange_accepts_simulator_log(monkeypatch, tmp_path):
    recorded = {}

    class _Recorder:
        def __init__(self, initial_usdt=50.0, simulator_log=None, load_existing=True):
            recorded["initial_usdt"] = initial_usdt
            recorded["simulator_log"] = simulator_log
            recorded["load_existing"] = load_existing

    monkeypatch.setattr("pt_exchange_api.KuCoinSimulator", _Recorder)
    initialize_exchange(mode="SIMULATOR", initial_usdt=12.5, simulator_log=str(tmp_path / "paper.json"))

    assert recorded["initial_usdt"] == 12.5
    assert recorded["simulator_log"].endswith("paper.json")
    assert recorded["load_existing"] is True
