import math


def sma(values, period):
    if len(values) < period:
        return 0.0
    return sum(values[-period:]) / period


def ema(values, period):
    if not values:
        return 0.0

    if len(values) < period:
        return sum(values) / len(values)

    value = sum(values[:period]) / period
    k = 2 / (period + 1)

    for price in values[period:]:
        value = price * k + value * (1 - k)

    return value


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def macd(values, fast=12, slow=26, signal=9):

    if len(values) < slow + signal:
        return 0.0, 0.0, 0.0

    # Optimization: Calculate fast and slow EMAs in a single O(n) pass
    # instead of recomputing ema(values[:i+1]) at each index (which was O(n^2)).
    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)

    fast_val = 0.0
    slow_val = 0.0
    s_fast = 0.0
    s_slow = 0.0

    line = [0.0] * len(values)

    for i, price in enumerate(values):
        if i < fast - 1:
            s_fast += price
            fast_val = s_fast / (i + 1)
        elif i == fast - 1:
            s_fast += price
            fast_val = s_fast / fast
        else:
            fast_val = price * k_fast + fast_val * (1 - k_fast)

        if i < slow - 1:
            s_slow += price
            slow_val = s_slow / (i + 1)
        elif i == slow - 1:
            s_slow += price
            slow_val = s_slow / slow
        else:
            slow_val = price * k_slow + slow_val * (1 - k_slow)

        line[i] = fast_val - slow_val

    signal_line = ema(
        line,
        signal,
    )

    return (
        line[-1],
        signal_line,
        line[-1] - signal_line,
    )


def atr(highs, lows, closes, period=14):

    if len(closes) < period + 1:
        return 0.0

    tr = []

    for i in range(1, len(closes)):

        tr.append(
            max(
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
        )

    return sma(
        tr,
        period,
    )


def adx(
    highs,
    lows,
    closes,
    period=14,
):

    if len(closes) < period * 2:
        return 0.0, 0.0, 0.0

    plus_dm = []
    minus_dm = []
    trs = []

    for i in range(1, len(closes)):

        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]

        plus_dm.append(
            up if up > down and up > 0 else 0
        )

        minus_dm.append(
            down
            if down > up and down > 0
            else 0
        )

        trs.append(
            max(
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
        )

    atr_value = sma(
        trs,
        period,
    )

    if atr_value <= 0:
        return 0.0, 0.0, 0.0

    plus = (
        sma(plus_dm, period)
        / atr_value
        * 100
    )

    minus = (
        sma(minus_dm, period)
        / atr_value
        * 100
    )

    dx = (
        abs(plus - minus)
        / max(plus + minus, 0.000001)
        * 100
    )

    return dx, plus, minus


def ichimoku(
    highs,
    lows,
    closes,
    conversion=20,
    base=60,
    span=120,
    displacement=30,
):

    if len(closes) < span:
        return {
            "tenkan": 0.0,
            "kijun": 0.0,
            "senkou_a": 0.0,
            "senkou_b": 0.0,
            "cloud_top": 0.0,
            "cloud_bottom": 0.0,
            "above_cloud": False,
            "bullish": False,
        }

    def mid(period, end):
        h = highs[end - period:end]
        l = lows[end - period:end]

        if not h or not l:
            return 0.0

        return (
            max(h) + min(l)
        ) / 2

    end = len(closes)

    tenkan = mid(
        conversion,
        end,
    )

    kijun = mid(
        base,
        end,
    )

    span_a = (
        tenkan + kijun
    ) / 2

    span_b = mid(
        span,
        end,
    )

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    price = closes[-1]

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": span_a,
        "senkou_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "above_cloud": price > cloud_top,
        "bullish": (
            price > cloud_top
            and tenkan > kijun
            and span_a > span_b
        ),
    }


def fibonacci(
    highs,
    lows,
    closes,
):

    if len(closes) < 20:
        return {}

    low_index = min(
        range(len(lows)),
        key=lambda i: lows[i],
    )

    high_index = max(
        range(low_index, len(highs)),
        key=lambda i: highs[i],
    )

    low = lows[low_index]
    high = highs[high_index]

    if high <= low:
        return {}

    distance = high - low

    return {
        "low": low,
        "high": high,
        "0.5": high - distance * 0.5,
        "0.618": high - distance * 0.618,
        "0.786": high - distance * 0.786,
    }


def volume_profile(
    highs,
    lows,
    closes,
    volumes,
    bins=50,
    value_area=70,
):

    if not closes:
        return {}

    low = min(lows)
    high = max(highs)

    if high <= low:
        return {}

    step = (
        high - low
    ) / bins

    profile = [0.0] * bins

    for i in range(len(closes)):

        index = int(
            (closes[i] - low)
            / step
        )

        index = max(
            0,
            min(
                bins - 1,
                index,
            ),
        )

        profile[index] += volumes[i]

    poc_index = max(
        range(bins),
        key=lambda i: profile[i],
    )

    poc = (
        low
        + (poc_index + 0.5) * step
    )

    total = sum(profile)

    target = (
        total
        * value_area
        / 100
    )

    order = sorted(
        range(bins),
        key=lambda i: profile[i],
        reverse=True,
    )

    accumulated = 0.0
    selected = []

    for index in order:

        accumulated += profile[index]
        selected.append(index)

        if accumulated >= target:
            break

    value_low = (
        low
        + min(selected) * step
    )

    value_high = (
        low
        + (max(selected) + 1) * step
    )

    return {
        "poc": poc,
        "value_low": value_low,
        "value_high": value_high,
    }


def fibonacci_distance(
    price,
    level,
):

    if price <= 0 or level <= 0:
        return 999.0

    return abs(
        price - level
    ) / price * 100


def td_sequential(
    closes,
):

    if len(closes) < 5:
        return {
            "setup": 0,
            "direction": "",
            "countdown": 0,
        }

    setup = 0
    direction = ""

    for i in range(
        len(closes) - 1,
        3,
        -1,
    ):

        if closes[i] > closes[i - 4]:

            if direction == "up":
                setup += 1
            else:
                setup = 1
                direction = "up"

        elif closes[i] < closes[i - 4]:

            if direction == "down":
                setup += 1
            else:
                setup = 1
                direction = "down"

        else:
            setup = 0
            direction = ""

        if setup >= 13:
            break

    return {
        "setup": setup,
        "direction": direction,
        "countdown": min(
            setup,
            13,
        ),
    }


def vwap(
    highs,
    lows,
    closes,
    volumes,
):

    total_volume = sum(volumes)

    if total_volume <= 0:
        return 0.0

    total = 0.0

    for h, l, c, v in zip(
        highs,
        lows,
        closes,
        volumes,
    ):

        typical = (
            h + l + c
        ) / 3

        total += typical * v

    return total / total_volume


def volume_ratio(
    volumes,
    period=20,
):

    if len(volumes) < period + 1:
        return 0.0

    old = sma(
        volumes[-period - 1:-1],
        period,
    )

    if old <= 0:
        return 0.0

    return (
        volumes[-1] / old
    )
