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


# ============================================================
# EMA
# ============================================================

def ema(v, period):
    if not v:
        return 0.0

    if period <= 0:
        return avg(v)

    if len(v) < period:
        return avg(v)

    k = 2 / (period + 1)
    e = avg(v[:period])

    for x in v[period:]:
        e = x * k + e * (1 - k)

    return e


def ema_series(v, period):
    if period <= 0 or len(v) < period:
        return [None] * len(v)

    k = 2 / (period + 1)
    e = avg(v[:period])

    out = [None] * (period - 1) + [e]

    for x in v[period:]:
        e = x * k + e * (1 - k)
        out.append(e)

    return out


# ============================================================
# RSI
# ============================================================

def rsi(v, period=14):
    if len(v) < period + 1:
        return 50.0

    d = [
        v[i] - v[i - 1]
        for i in range(1, len(v))
    ]

    gains = [max(x, 0) for x in d]
    losses = [max(-x, 0) for x in d]

    g = avg(gains[:period])
    l = avg(losses[:period])

    for i in range(period, len(d)):
        g = (
            g * (period - 1)
            + gains[i]
        ) / period

        l = (
            l * (period - 1)
            + losses[i]
        ) / period

    if l == 0:
        return 100.0

    rs = g / l

    return 100 - 100 / (1 + rs)


# ============================================================
# MACD
# ============================================================

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
    histogram = main - signal

    return main, signal, histogram


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

    signal = ema_series(line, 9)

    return [
        a - b
        if b is not None
        else 0.0
        for a, b in zip(line, signal)
    ]


# ============================================================
# BOLLINGER BANDS
# ============================================================

def bb(v, period=20, k=2):
    if len(v) < period:
        return 0.0, 0.0, 0.0

    s = v[-period:]

    middle = avg(s)

    variance = avg([
        (x - middle) ** 2
        for x in s
    ])

    sd = math.sqrt(variance)

    lower = middle - k * sd
    upper = middle + k * sd

    return lower, middle, upper


# ============================================================
# ATR
# ============================================================

def atr(
    highs,
    lows,
    closes,
    period=14,
):
    if len(closes) < period + 1:
        return 0.0

    tr = []

    for i in range(1, len(closes)):
        value = max(
            highs[i] - lows[i],
            abs(
                highs[i]
                - closes[i - 1]
            ),
            abs(
                lows[i]
                - closes[i - 1]
            ),
        )

        tr.append(value)

    if len(tr) < period:
        return 0.0

    value = avg(tr[:period])

    for x in tr[period:]:
        value = (
            value * (period - 1)
            + x
        ) / period

    return value


# ============================================================
# ADX / DI
# ============================================================

def adx(
    highs,
    lows,
    closes,
    period=14,
):
    if len(closes) < period * 2 + 1:
        return 0.0, 0.0, 0.0

    tr = []
    plus = []
    minus = []

    for i in range(1, len(closes)):

        true_range = max(
            highs[i] - lows[i],
            abs(
                highs[i]
                - closes[i - 1]
            ),
            abs(
                lows[i]
                - closes[i - 1]
            ),
        )

        tr.append(true_range)

        up = (
            highs[i]
            - highs[i - 1]
        )

        down = (
            lows[i - 1]
            - lows[i]
        )

        plus.append(
            up
            if up > down and up > 0
            else 0.0
        )

        minus.append(
            down
            if down > up and down > 0
            else 0.0
        )

    if len(tr) < period:
        return 0.0, 0.0, 0.0

    tr_sum = sum(tr[:period])
    plus_sum = sum(plus[:period])
    minus_sum = sum(minus[:period])

    dxs = []

    for i in range(period, len(tr)):

        tr_sum = (
            tr_sum
            - tr_sum / period
            + tr[i]
        )

        plus_sum = (
            plus_sum
            - plus_sum / period
            + plus[i]
        )

        minus_sum = (
            minus_sum
            - minus_sum / period
            + minus[i]
        )

        if tr_sum == 0:
            continue

        plus_di = (
            100
            * plus_sum
            / tr_sum
        )

        minus_di = (
            100
            * minus_sum
            / tr_sum
        )

        total = plus_di + minus_di

        if total:
            dxs.append(
                100
                * abs(
                    plus_di
                    - minus_di
                )
                / total
            )
        else:
            dxs.append(0.0)

    if not dxs:
        return 0.0, 0.0, 0.0

    if len(dxs) < period:
        adx_value = avg(dxs)
    else:
        adx_value = avg(
            dxs[:period]
        )

        for x in dxs[period:]:
            adx_value = (
                adx_value * (period - 1)
                + x
            ) / period

    plus_di = (
        100 * plus_sum / tr_sum
        if tr_sum
        else 0.0
    )

    minus_di = (
        100 * minus_sum / tr_sum
        if tr_sum
        else 0.0
    )

    return (
        adx_value,
        plus_di,
        minus_di,
    )


# ============================================================
# OBV
# ============================================================

def obv(closes, volumes):
    if not closes:
        return []

    out = [0.0]

    for i in range(1, len(closes)):

        if closes[i] > closes[i - 1]:
            out.append(
                out[-1] + volumes[i]
            )

        elif closes[i] < closes[i - 1]:
            out.append(
                out[-1] - volumes[i]
            )

        else:
            out.append(
                out[-1]
            )

    return out


# ============================================================
# KELTNER CHANNEL
# ============================================================

def keltner_channel(
    highs,
    lows,
    closes,
    period=20,
    multiplier=1.5,
):
    if len(closes) < period:
        return 0.0, 0.0, 0.0

    middle = ema(
        closes,
        period,
    )

    band = (
        atr(
            highs,
            lows,
            closes,
            period,
        )
        * multiplier
    )

    return (
        middle - band,
        middle,
        middle + band,
    )


# ============================================================
# BULLISH DIVERGENCE
# ============================================================

def bullish_divergence(
    price,
    indicator,
    lookback=40,
):
    if (
        len(price) < lookback
        or len(indicator) < lookback
    ):
        return False

    p = price[-lookback:]
    ind = indicator[-lookback:]

    lows = []

    for i in range(
        2,
        len(p) - 2,
    ):
        if (
            p[i] <= p[i - 1]
            and p[i] <= p[i + 1]
        ):
            lows.append(i)

    if len(lows) < 2:
        return False

    a = lows[-2]
    b = lows[-1]

    return (
        p[b] < p[a]
        and ind[b] > ind[a]
    )


# ============================================================
# VWAP
# ============================================================

def vwap(
    highs,
    lows,
    closes,
    volumes,
):
    if not closes:
        return 0.0

    pv = 0.0
    vol = 0.0

    for h, l, c, volume in zip(
        highs,
        lows,
        closes,
        volumes,
    ):

        if volume <= 0:
            continue

        typical_price = (
            h + l + c
        ) / 3

        pv += (
            typical_price
            * volume
        )

        vol += volume

    if vol <= 0:
        return 0.0

    return pv / vol
