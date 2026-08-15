from __future__ import annotations


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct(a: float, b: float | None) -> float:
    if not a or b is None:
        return 0.0
    return ((b - a) / a) * 100


def clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def soft_cap(value: float, cap: float, factor: float) -> float:
    if value <= cap:
        return value
    return cap + (value - cap) * factor


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
    """Wilder RSI."""
    if len(values) < period + 1:
        return 50.0

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = avg(gains[:period])
    avg_loss = avg(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def ema_series(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    if len(values) < period:
        return [None] * len(values)

    k = 2 / (period + 1)
    series: list[float | None] = [None] * (period - 1)
    result = avg(values[:period])
    series.append(result)

    for value in values[period:]:
        result = value * k + result * (1 - k)
        series.append(result)

    return series


def macd(values: list[float]) -> tuple[float, float, float]:
    """EMA-serisi kullanarak O(n) MACD."""
    if len(values) < 35:
        return 0, 0, 0

    ema12 = ema_series(values, 12)
    ema26 = ema_series(values, 26)

    macd_line = [
        e12 - e26
        for e12, e26 in zip(ema12, ema26)
        if e12 is not None and e26 is not None
    ]

    if not macd_line:
        return 0, 0, 0

    main = macd_line[-1]
    signal = ema(macd_line, 9)
    return main, signal, main - signal


def bb(
    values: list[float],
    period: int = 20,
    k: float = 2,
) -> tuple[float, float, float]:
    if len(values) < period:
        return 0, 0, 0

    sample = values[-period:]
    middle = avg(sample)
    deviation = avg([(x - middle) ** 2 for x in sample]) ** 0.5

    return middle - k * deviation, middle, middle + k * deviation


def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[float, float, float]:
    """Wilder ADX -> (ADX, +DI, -DI)."""
    if len(closes) < period * 2 + 1:
        return 0, 0, 0

    trs, plus_dms, minus_dms = [], [], []

    for i in range(1, len(closes)):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]

        plus_dms.append(up if up > down and up > 0 else 0)
        minus_dms.append(down if down > up and down > 0 else 0)

    atr = sum(trs[:period])
    plus_sum = sum(plus_dms[:period])
    minus_sum = sum(minus_dms[:period])

    def _dx(
        atr_v: float,
        plus_v: float,
        minus_v: float,
    ) -> tuple[float, float, float]:
        if atr_v <= 0:
            return 0.0, 0.0, 0.0

        plus_di = 100 * plus_v / atr_v
        minus_di = 100 * minus_v / atr_v
        total = plus_di + minus_di
        dx_v = 100 * abs(plus_di - minus_di) / total if total else 0.0

        return dx_v, plus_di, minus_di

    dx, plus_di, minus_di = _dx(atr, plus_sum, minus_sum)
    dx_series = [dx]

    for i in range(period, len(trs)):
        atr = atr - (atr / period) + trs[i]
        plus_sum = plus_sum - (plus_sum / period) + plus_dms[i]
        minus_sum = minus_sum - (minus_sum / period) + minus_dms[i]

        dx, plus_di, minus_di = _dx(atr, plus_sum, minus_sum)
        dx_series.append(dx)

    if len(dx_series) < period:
        adx_value = avg(dx_series)
    else:
        adx_value = avg(dx_series[:period])
        for value in dx_series[period:]:
            adx_value = (adx_value * (period - 1) + value) / period

    return adx_value, plus_di, minus_di
    
