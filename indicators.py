"""
Saf matematik fonksiyonları: RSI, MACD, ADX, Bollinger Bands, EMA.

Bunların hiçbiri ağ, DB veya global state'e dokunmuyor -> birim testi
yazmak trivial. Eski kodda bu fonksiyonlar da aynıydı ama tek bir 900
satırlık dosyanın içinde kayboluyorlardı; buraya taşımak davranışı
DEĞİŞTİRMEZ, sadece test edilebilir ve tekrar kullanılabilir hale getirir.
"""

from __future__ import annotations


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct(a: float, b: float | None) -> float:
    if not a or b is None:
        return 0.0
    return ((b - a) / a) * 100


def clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return avg(values)

    k = 2 / (period + 1)
    result = avg(values[:period])
    for value in values[period:]:
        result = value * k + result * (1 - k)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0

    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    gain = avg(gains[-period:])
    loss = avg(losses[-period:])

    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def macd(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 35:
        return 0, 0, 0

    values_macd = [
        ema(values[:i], 12) - ema(values[:i], 26)
        for i in range(26, len(values) + 1)
    ]

    main = values_macd[-1]
    signal = ema(values_macd, 9)
    return main, signal, main - signal


def bb(values: list[float], period: int = 20, k: float = 2) -> tuple[float, float, float]:
    if len(values) < period:
        return 0, 0, 0

    sample = values[-period:]
    middle = avg(sample)
    deviation = avg([(x - middle) ** 2 for x in sample]) ** 0.5

    return middle - k * deviation, middle, middle + k * deviation


def adx(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> tuple[float, float, float]:
    if len(closes) < period * 2 + 1:
        return 0, 0, 0

    tr, plus, minus = [], [], []
    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)

    atr = avg(tr[-period:])
    p = avg(plus[-period:])
    m = avg(minus[-period:])

    if atr <= 0:
        return 0, 0, 0

    plus_di = 100 * p / atr
    minus_di = 100 * m / atr
    total = plus_di + minus_di
    dx = 100 * abs(plus_di - minus_di) / total if total else 0

    return dx, plus_di, minus_di
  
