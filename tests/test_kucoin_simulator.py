import pytest

from pt_kucoin_simulator import KuCoinSimulator


def test_simulator_buy_and_balance_update(monkeypatch, tmp_path):
    sim = KuCoinSimulator(initial_usdt=100.0, simulator_log=str(tmp_path / "simulator_trades.json"))

    # Mock price to avoid network dependency
    monkeypatch.setattr(sim, "get_current_price", lambda symbol: 10.0)

    # Place a buy for 1 unit at mocked price $10 -> cost $10
    result = sim.place_market_order("BTC-USDT", "buy", 1.0)

    assert result.get("status") == "success"
    assert result.get("orderId") is not None

    balance = sim.get_account_balance()
    assert balance["usdt"] == pytest.approx(90.0)
    assert "BTC-USDT" in balance["positions"]
    assert balance["positions"]["BTC-USDT"]["qty"] == pytest.approx(1.0)


def test_simulator_reset_clears_state(monkeypatch, tmp_path):
    sim = KuCoinSimulator(initial_usdt=100.0, simulator_log=str(tmp_path / "simulator_trades.json"))
    monkeypatch.setattr(sim, "get_current_price", lambda symbol: 20.0)

    result = sim.place_market_order("BTC-USDT", "buy", 1.0)
    assert result.get("status") == "success"

    sim.reset_state(initial_usdt=25.0)
    balance = sim.get_account_balance()

    assert balance["usdt"] == pytest.approx(25.0)
    assert balance["positions"] == {}
    assert balance["trades_closed"] == 0
