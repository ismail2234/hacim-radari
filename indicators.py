from __future__ import annotations

from math import sqrt


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct(a: float, b: float | None) -> float:
    if a == 0 or b is None:
        return 0.0
    return (b - a) / a * 100.0


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def ema(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return 0.0

    if len(values) < period:
        return avg(values)

    multiplier = 2.0 / (period + 1.0)
    result = avg(values[:period])

    for value in values[period:]:
        result = (value - result) * multiplier + result

    return result


def rsi(values: list[float], period: int = 14) -> float:
    if period <= 0 or len(values) < period + 1:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []

    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    gain = avg(gains[-period:])
    loss = avg(losses[-period:])

    if loss == 0:
        return 100.0

    if gain == 0:
        return 0.0

    relative_strength = gain / loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float]:
    minimum = slow + signal_period

    if len(values) < minimum:
        return 0.0, 0.0, 0.0

    macd_values: list[float] = []

    for end in range(slow, len(values) + 1):
        sample = values[:end]
        fast_ema = ema(sample, fast)
        slow_ema = ema(sample, slow)
        macd_values.append(fast_ema - slow_ema)

    if not macd_values:
        return 0.0, 0.0, 0.0

    main = macd_values[-1]
    signal = ema(macd_values, signal_period)
    histogram = main - signal

    return main, signal, histogram


def bb(
    values: list[float],
    period: int = 20,
    k: float = 2.0,
) -> tuple[float, float, float]:
    if period <= 0 or len(values) < period:
        return 0.0, 0.0, 0.0

    sample = values[-period:]
    middle = avg(sample)

    variance = avg(
        [(value - middle) ** 2 for value in sample]
    )
    deviation = sqrt(max(variance, 0.0))

    return (
        middle - k * deviation,
        middle,
        middle + k * deviation,
    )


def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[float, float, float]:
    size = min(len(highs), len(lows), len(closes))

    if period <= 0 or size < period * 2 + 1:
        return 0.0, 0.0, 0.0

    highs = highs[:size]
    lows = lows[:size]
    closes = closes[:size]

    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []

    for i in range(1, size):
        previous_close = closes[i - 1]

        true_range = max(
            highs[i] - lows[i],
            abs(highs[i] - previous_close),
            abs(lows[i] - previous_close),
        )

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        positive_dm = (
            up_move
            if up_move > down_move and up_move > 0
            else 0.0
        )

        negative_dm = (
            down_move
            if down_move > up_move and down_move > 0
            else 0.0
        )

        true_ranges.append(max(true_range, 0.0))
        plus_dm.append(positive_dm)
        minus_dm.append(negative_dm)

    if len(true_ranges) < period:
        return 0.0, 0.0, 0.0

    atr = avg(true_ranges[-period:])
    positive = avg(plus_dm[-period:])
    negative = avg(minus_dm[-period:])

    if atr <= 0:
        return 0.0, 0.0, 0.0

    plus_di = 100.0 * positive / atr
    minus_di = 100.0 * negative / atr

    denominator = plus_di + minus_di

    if denominator <= 0:
        return 0.0, plus_di, minus_di

    dx = (
        100.0
        * abs(plus_di - minus_di)
        / denominator
    )

    return dx, plus_di, minus_di
