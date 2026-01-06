import importlib
import json
from pathlib import Path


def _make_candles(descending: bool = True, count: int = 60):
    candles = []
    base = 100.0
    for i in range(count):
        price = base - i if descending else base + i
        candles.append(
            {
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 100.0 + i,
            }
        )
    return candles


def _write_gui_settings(tmp_path: Path, strategy: dict) -> None:
    data = {
        "main_neural_dir": str(tmp_path),
        "coins": ["BNB"],
        "default_timeframe": "1hour",
        "candles_limit": 120,
        "strategy": strategy,
    }
    (tmp_path / "gui_settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_selector_mode_with_neural(tmp_path, monkeypatch):
    strategy = {
        "mode": "selector",
        "indicators": {
            "rsi": True,
        },
        "check_all": False,
        "replace_neural": False,
    }
    _write_gui_settings(tmp_path, strategy)
    monkeypatch.setenv("POWERTRADER_GUI_SETTINGS", str(tmp_path / "gui_settings.json"))
    monkeypatch.setenv("EXCHANGE_PROVIDER", "binance")
    monkeypatch.setenv("BINANCE_PAPER", "true")

    import pt_trader
    importlib.reload(pt_trader)
    trader = pt_trader.CryptoAPITrading()
    candles = _make_candles(descending=True)
    allowed, _ = trader._strategy_should_enter("BNB", long_level=3, short_level=0, candles=candles)
    assert allowed is True

    allowed, _ = trader._strategy_should_enter("BNB", long_level=3, short_level=1, candles=candles)
    assert allowed is False


def test_selector_replace_neural(tmp_path, monkeypatch):
    strategy = {
        "mode": "selector",
        "indicators": {
            "rsi": True,
        },
        "check_all": False,
        "replace_neural": True,
    }
    _write_gui_settings(tmp_path, strategy)
    monkeypatch.setenv("POWERTRADER_GUI_SETTINGS", str(tmp_path / "gui_settings.json"))
    monkeypatch.setenv("EXCHANGE_PROVIDER", "binance")
    monkeypatch.setenv("BINANCE_PAPER", "true")

    import pt_trader
    importlib.reload(pt_trader)
    trader = pt_trader.CryptoAPITrading()
    candles = _make_candles(descending=True)
    allowed, _ = trader._strategy_should_enter("BNB", long_level=0, short_level=1, candles=candles)
    assert allowed is True


def test_super_mode_score(monkeypatch, tmp_path):
    strategy = {
        "mode": "super",
        "indicators": {
            "rsi": True,
            "momentum": True,
        },
        "check_all": False,
        "replace_neural": False,
    }
    _write_gui_settings(tmp_path, strategy)
    monkeypatch.setenv("POWERTRADER_GUI_SETTINGS", str(tmp_path / "gui_settings.json"))
    monkeypatch.setenv("EXCHANGE_PROVIDER", "binance")
    monkeypatch.setenv("BINANCE_PAPER", "true")

    import pt_trader
    importlib.reload(pt_trader)
    trader = pt_trader.CryptoAPITrading()
    candles = _make_candles(descending=True)
    allowed, score = trader._strategy_should_enter("BNB", long_level=7, short_level=0, candles=candles)
    assert allowed is True
    assert score >= 0.6

    strategy["replace_neural"] = True
    _write_gui_settings(tmp_path, strategy)
    allowed, score = trader._strategy_should_enter("BNB", long_level=7, short_level=0, candles=candles)
    assert allowed is False
    assert score < 0.6
