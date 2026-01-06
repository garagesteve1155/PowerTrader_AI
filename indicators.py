import math
from typing import Dict, List, Optional, Tuple


def sma(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / float(period)


def ema(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema_val = sum(values[:period]) / float(period)
    for v in values[period:]:
        ema_val = (v * k) + (ema_val * (1.0 - k))
    return ema_val


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if period <= 0 or len(closes) < (period + 1):
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / float(period)
    avg_loss = sum(losses) / float(period)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < slow:
        return None, None, None
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    if not ema_fast or not ema_slow:
        return None, None, None
    macd_series = [f - s for f, s in zip(ema_fast[-len(ema_slow):], ema_slow)]
    signal_series = _ema_series(macd_series, signal)
    if not signal_series:
        return None, None, None
    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def stochastic(highs: List[float], lows: List[float], closes: List[float], k_period: int = 14, d_period: int = 3) -> Tuple[Optional[float], Optional[float]]:
    if len(closes) < k_period or len(highs) < k_period or len(lows) < k_period:
        return None, None
    recent_high = max(highs[-k_period:])
    recent_low = min(lows[-k_period:])
    if recent_high == recent_low:
        return 50.0, 50.0
    k = ((closes[-1] - recent_low) / (recent_high - recent_low)) * 100.0
    k_series = []
    for i in range(-k_period, 0):
        h = max(highs[:i + len(highs) + 1][-k_period:])
        l = min(lows[:i + len(lows) + 1][-k_period:])
        if h == l:
            k_series.append(50.0)
        else:
            k_series.append(((closes[i] - l) / (h - l)) * 100.0)
    d = sma(k_series, d_period) if k_series else None
    return k, d


def momentum(closes: List[float], period: int = 10) -> Optional[float]:
    if len(closes) < (period + 1):
        return None
    return closes[-1] - closes[-1 - period]


def obv(closes: List[float], volumes: List[float]) -> Optional[float]:
    if len(closes) < 2 or len(volumes) < 2:
        return None
    obv_val = 0.0
    for i in range(1, min(len(closes), len(volumes))):
        if closes[i] > closes[i - 1]:
            obv_val += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv_val -= volumes[i]
    return obv_val


def bollinger_bands(closes: List[float], period: int = 20, std_mult: float = 2.0) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mean = sum(window) / float(period)
    variance = sum((x - mean) ** 2 for x in window) / float(period)
    std = math.sqrt(variance)
    upper = mean + std_mult * std
    lower = mean - std_mult * std
    return upper, mean, lower


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < (period + 1) or len(highs) < (period + 1) or len(lows) < (period + 1):
        return None
    trs = []
    for i in range(-period, 0):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / float(period)


def volume_profile(volumes: List[float], period: int = 20) -> Optional[float]:
    if len(volumes) < period:
        return None
    avg_vol = sum(volumes[-period:]) / float(period)
    if avg_vol == 0:
        return 0.0
    return volumes[-1] / avg_vol


def adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < (period + 1):
        return None
    plus_dm = []
    minus_dm = []
    tr_list = []
    for i in range(-period, 0):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)
    tr_sum = sum(tr_list)
    if tr_sum == 0:
        return 0.0
    plus_di = 100.0 * (sum(plus_dm) / tr_sum)
    minus_di = 100.0 * (sum(minus_dm) / tr_sum)
    denom = plus_di + minus_di
    if denom == 0:
        return 0.0
    dx = (abs(plus_di - minus_di) / denom) * 100.0
    return dx


def pivots(highs: List[float], lows: List[float], closes: List[float]) -> Optional[Dict[str, float]]:
    if not highs or not lows or not closes:
        return None
    h = highs[-1]
    l = lows[-1]
    c = closes[-1]
    p = (h + l + c) / 3.0
    r1 = 2 * p - l
    s1 = 2 * p - h
    r2 = p + (h - l)
    s2 = p - (h - l)
    return {"pivot": p, "r1": r1, "s1": s1, "r2": r2, "s2": s2}


def ichimoku(highs: List[float], lows: List[float]) -> Optional[Dict[str, float]]:
    if len(highs) < 52 or len(lows) < 52:
        return None
    tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2.0
    kijun = (max(highs[-26:]) + min(lows[-26:])) / 2.0
    senkou_a = (tenkan + kijun) / 2.0
    senkou_b = (max(highs[-52:]) + min(lows[-52:])) / 2.0
    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b}


def _ema_series(values: List[float], period: int) -> List[float]:
    if period <= 0 or len(values) < period:
        return []
    k = 2.0 / (period + 1.0)
    ema_val = sum(values[:period]) / float(period)
    out = [ema_val]
    for v in values[period:]:
        ema_val = (v * k) + (ema_val * (1.0 - k))
        out.append(ema_val)
    return out
