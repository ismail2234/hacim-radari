from __future__ import annotations
import math


def avg(v):
    return sum(v) / len(v) if v else 0.0


def pct(a, b):
    if not a or b is None:
        return 0.0
    return (b - a) / a * 100


def clamp(x):
    return max(0, min(100, int(round(x))))


def soft_cap(x, cap, factor):
    return x if x <= cap else cap + (x - cap) * factor


def ema(v, period):
    if not v:
        return 0.0
    if len(v) < period:
        return avg(v)

    k = 2 / (period + 1)
    e = avg(v[:period])

    for x in v[period:]:
        e = x * k + e * (1 - k)

    return e


def ema_series(v, period):
    if len(v) < period:
        return [None] * len(v)

    k = 2 / (period + 1)
    e = avg(v[:period])
    out = [None] * (period - 1) + [e]

    for x in v[period:]:
        e = x * k + e * (1 - k)
        out.append(e)

    return out


def rsi(v, period=14):
    if len(v) < period + 1:
        return 50.0

    d = [v[i] - v[i - 1] for i in range(1, len(v))]
    gains = [max(x, 0) for x in d]
    losses = [max(-x, 0) for x in d]

    g = avg(gains[:period])
    l = avg(losses[:period])

    for i in range(period, len(d)):
        g = (g * (period - 1) + gains[i]) / period
        l = (l * (period - 1) + losses[i]) / period

    if l == 0:
        return 100.0

    return 100 - 100 / (1 + g / l)


def macd(v):
    if len(v) < 35:
        return 0.0, 0.0, 0.0

    e12 = ema_series(v, 12)
    e26 = ema_series(v, 26)

    line = [
        a - b
        for a, b in zip(e12, e26)
        if a is not None and b is not None
    ]

    if not line:
        return 0.0, 0.0, 0.0

    main = line[-1]
    signal = ema(line, 9)

    return main, signal, main - signal


def macd_hist_series(v):
    if len(v) < 35:
        return []

    e12 = ema_series(v, 12)
    e26 = ema_series(v, 26)

    line = [
        a - b
        for a, b in zip(e12, e26)
        if a is not None and b is not None
    ]

    if len(line) < 9:
        return []

    sig = ema_series(line, 9)

    return [
        a - b if b is not None else 0.0
        for a, b in zip(line, sig)
    ]


def bb(v, period=20, k=2):
    if len(v) < period:
        return 0.0, 0.0, 0.0

    s = v[-period:]
    m = avg(s)
    sd = math.sqrt(avg([(x - m) ** 2 for x in s]))

    return m - k * sd, m, m + k * sd


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0

    tr = []

    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

    a = avg(tr[:period])

    for x in tr[period:]:
        a = (a * (period - 1) + x) / period

    return a


def adx(highs, lows, closes, period=14):
    if len(closes) < period * 2 + 1:
        return 0.0, 0.0, 0.0

    tr = []
    plus = []
    minus = []

    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]

        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)

    a = sum(tr[:period])
    p = sum(plus[:period])
    m = sum(minus[:period])

    dxs = []

    for i in range(period, len(tr)):
        a = a - a / period + tr[i]
        p = p - p / period + plus[i]
        m = m - m / period + minus[i]

        if a == 0:
            continue

        pdi = 100 * p / a
        mdi = 100 * m / a
        total = pdi + mdi

        dxs.append(
            100 * abs(pdi - mdi) / total
            if total else 0
        )

    if not dxs:
        return 0.0, 0.0, 0.0

    adx_value = avg(dxs[:period])

    for x in dxs[period:]:
        adx_value = (
            adx_value * (period - 1) + x
        ) / period

    pdi = 100 * p / a if a else 0
    mdi = 100 * m / a if a else 0

    return adx_value, pdi, mdi


def obv(closes, volumes):
    if not closes:
        return []

    out = [0.0]

    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            out.append(out[-1] - volumes[i])
        else:
            out.append(out[-1])

    return out


def keltner_channel(
    highs,
    lows,
    closes,
    period=20,
    multiplier=1.5
):
    if len(closes) < period:
        return 0.0, 0.0, 0.0

    middle = ema(closes, period)
    band = atr(highs, lows, closes, period) * multiplier

    return (
        middle - band,
        middle,
        middle + band
    )


def bullish_divergence(
    price,
    indicator,
    lookback=40
):
    if len(price) < lookback:
        return False

    p = price[-lookback:]
    ind = indicator[-lookback:]

    lows = []

    for i in range(2, len(p) - 2):
        if p[i] <= p[i-1] and p[i] <= p[i+1]:
            lows.append(i)

    if len(lows) < 2:
        return False

    a, b = lows[-2], lows[-1]

    return (
        p[b] < p[a]
        and ind[b] > ind[a]
    )


def vwap(highs, lows, closes, volumes):
    if not closes:
        return 0.0

    pv = 0.0
    vol = 0.0

    for h, l, c, v in zip
