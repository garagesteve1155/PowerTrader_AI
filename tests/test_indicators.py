import math

import indicators


def test_sma():
    values = [1, 2, 3, 4, 5]
    assert indicators.sma(values, 5) == 3.0


def test_ema():
    values = [1, 2, 3, 4, 5]
    ema_val = indicators.ema(values, 3)
    assert ema_val is not None
    assert math.isclose(ema_val, 4.0, rel_tol=1e-6)


def test_rsi():
    up = list(range(1, 20))
    down = list(range(20, 1, -1))
    assert indicators.rsi(up, period=14) > 70
    assert indicators.rsi(down, period=14) < 30


def test_macd():
    values = list(range(1, 60))
    macd_line, signal_line, hist = indicators.macd(values)
    assert macd_line is not None
    assert signal_line is not None
    assert hist is not None


def test_stochastic():
    closes = list(range(1, 21))
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    k, d = indicators.stochastic(highs, lows, closes)
    assert k is not None
    assert k > 80
    assert d is not None


def test_atr():
    highs = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
    lows = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
    closes = [9.5, 10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5, 20.5, 21.5, 22.5, 23.5]
    atr_val = indicators.atr(highs, lows, closes, period=14)
    assert atr_val is not None
    assert atr_val > 0
