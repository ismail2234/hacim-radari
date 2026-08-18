from indicators import (
    adx,
    fibonacci,
    fibonacci_distance,
    ichimoku,
    macd,
    rsi,
    td_sequential,
    volume_profile,
    volume_ratio,
    vwap,
)


BAD_SYMBOLS = {
    "USDTTRY",
    "USDCTRY",
    "BUSDTRY",
    "FDUSDTRY",
    "TUSDTRY",
    "DAITRY",
}


def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _last(values, default=0.0):
    if not values:
        return default

    value = values[-1]

    if isinstance(value, dict):
        for key in (
            "close",
            "value",
            "price",
            "rsi",
            "adx",
        ):
            if key in value:
                return _num(value[key])

    return _num(value, default)


def _get(row, *keys, default=0.0):
    if not isinstance(row, dict):
        return default

    for key in keys:
        if key in row:
            return _num(row[key], default)

    return default


def _closes(data):
    result = []

    for row in data:

        if isinstance(row, dict):
            value = _get(
                row,
                "close",
                "c",
                default=None,
            )
        else:
            try:
                value = float(row[4])
            except Exception:
                value = None

        if value is not None:
            result.append(
                float(value)
            )

    return result


def _highs(data):
    result = []

    for row in data:

        if isinstance(row, dict):
            value = _get(
                row,
                "high",
                "h",
                default=None,
            )
        else:
            try:
                value = float(row[2])
            except Exception:
                value = None

        if value is not None:
            result.append(
                float(value)
            )

    return result


def _lows(data):
    result = []

    for row in data:

        if isinstance(row, dict):
            value = _get(
                row,
                "low",
                "l",
                default=None,
            )
        else:
            try:
                value = float(row[3])
            except Exception:
                value = None

        if value is not None:
            result.append(
                float(value)
            )

    return result


def _volumes(data):
    result = []

    for row in data:

        if isinstance(row, dict):
            value = _get(
                row,
                "volume",
                "v",
                default=None,
            )
        else:
            try:
                value = float(row[5])
            except Exception:
                value = None

        if value is not None:
            result.append(
                float(value)
            )

    return result


def _call(func, *args, default=None):
    try:
        return func(*args)
    except Exception:
        return default


def _extract_value(value, keys, default=0.0):

    if isinstance(value, dict):

        for key in keys:

            if key in value:
                return value[key]

    return value if value is not None else default


def _td_value(value):

    value = _extract_value(
        value,
        (
            "setup",
            "count",
            "td",
            "value",
        ),
        0,
    )

    try:
        return int(float(value))
    except Exception:
        return 0


def _fib_values(value):

    if not isinstance(value, dict):
        return 0.0, 0.0, 0.0

    return (
        _num(
            value.get("0.5"),
            0,
        ),
        _num(
            value.get("0.618"),
            0,
        ),
        _num(
            value.get("0.786"),
            0,
        ),
    )


def _vp_values(value):

    if not isinstance(value, dict):
        return 0.0, 0.0, 0.0

    poc = _num(
        value.get(
            "poc",
            value.get("POC", 0),
        )
    )

    va_low = _num(
        value.get(
            "value_area_low",
            value.get("va_low", 0),
        )
    )

    va_high = _num(
        value.get(
            "value_area_high",
            value.get("va_high", 0),
        )
    )

    return poc, va_low, va_high


def _ichimoku_values(value):

    if not isinstance(value, dict):
        return False, False

    bullish = bool(
        value.get(
            "bullish",
            value.get(
                "trend_up",
                False,
            ),
        )
    )

    above = bool(
        value.get(
            "above_cloud",
            value.get(
                "above",
                False,
            ),
        )
    )

    return bullish, above


def analyze(
    cfg,
    client,
    symbol,
):

    symbol = str(
        symbol
    ).upper()

    if symbol in BAD_SYMBOLS:
        return None

    try:
        data = client.klines(
            symbol,
            "5m",
            getattr(
                cfg,
                "candles",
                250,
            ),
        )
    except Exception:
        return None

    if not data:
        return None

    if len(data) < 150:
        return None

    closes = _closes(data)
    highs = _highs(data)
    lows = _lows(data)
    volumes = _volumes(data)

    if len(closes) < 100:
        return None

    price = closes[-1]

    # Ichimoku
    ichi_raw = _call(
        ichimoku,
        data,
        20,
        60,
        120,
        30,
        default={},
    )

    ichi_bullish, above_cloud = (
        _ichimoku_values(
            ichi_raw
        )
    )

    # Fibonacci
    fib_raw = _call(
        fibonacci,
        highs,
        lows,
        default={},
    )

    fib50, fib618, fib786 = (
        _fib_values(
            fib_raw
        )
    )

    fib_distance_raw = _call(
        fibonacci_distance,
        price,
        fib_raw,
        default=999,
    )

    fib_distance_value = _num(
        _extract_value(
            fib_distance_raw,
            (
                "distance",
                "value",
            ),
            fib_distance_raw,
        ),
        999,
    )

    fib_zone = (
        fib_distance_value <= 1.0
        if fib_distance_value != 999
        else (
            (
                min(
                    fib618,
                    fib786,
                )
                <= price
                <= max(
                    fib618,
                    fib786,
                )
            )
            if fib618 and fib786
            else False
        )
    )

    # Volume Profile
    vp_raw = _call(
        volume_profile,
        data,
        70,
        default={},
    )

    poc, va_low, va_high = (
        _vp_values(
            vp_raw
        )
    )

    poc_near = (
        abs(price - poc)
        / price
        * 100
        <= 1.0
        if poc > 0
        else False
    )

    fib_poc = (
        fib_zone
        and poc_near
    )

    # TD Sequential
    td_raw = _call(
        td_sequential,
        closes,
        default=0,
    )

    td = _td_value(
        td_raw
    )

    # Hacim
    vr_raw = _call(
        volume_ratio,
        volumes,
        default=0,
    )

    volume_ratio_value = _num(
        _extract_value(
            vr_raw,
            (
                "ratio",
                "value",
            ),
            vr_raw,
        )
    )

    # RSI
    rsi_raw = _call(
        rsi,
        closes,
        default=0,
    )

    rsi_value = _num(
        _extract_value(
            rsi_raw,
            (
                "rsi",
                "value",
            ),
            rsi_raw,
        )
    )

    # MACD
    macd_raw = _call(
        macd,
        closes,
        default=False,
    )

    if isinstance(
        macd_raw,
        dict,
    ):
        macd_ok = bool(
            macd_raw.get(
                "bullish",
                macd_raw.get(
                    "positive",
                    False,
                ),
            )
        )
    else:
        macd_ok = bool(
            macd_raw
        )

    # ADX
    adx_raw = _call(
        adx,
        highs,
        lows,
        closes,
        default=0,
    )

    adx_value = _num(
        _extract_value(
            adx_raw,
            (
                "adx",
                "value",
            ),
            adx_raw,
        )
    )

    # VWAP
    vwap_raw = _call(
        vwap,
        data,
        default=0,
    )

    vwap_value = _num(
        _extract_value(
            vwap_raw,
            (
                "vwap",
                "value",
            ),
            vwap_raw,
        )
    )

    vwap_ok = (
        price >= vwap_value
        if vwap_value > 0
        else False
    )

    # Skor
    score = 0
    criteria = []

    if ichi_bullish:
        score += 15
        criteria.append(
            "Ichimoku yükseliş"
        )

    if above_cloud:
        score += 10
        criteria.append(
            "Bulut üstü"
        )

    if fib_zone:
        score += 15
        criteria.append(
            "Fib bölgesi"
        )

    if poc_near:
        score += 10
        criteria.append(
            "POC yakın"
        )

    if fib_poc:
        score += 15
        criteria.append(
            "Fib+POC kesişimi"
        )

    if volume_ratio_value >= 2:
        score += 10
        criteria.append(
            f"Hacim {volume_ratio_value:.1f}x"
        )

    if rsi_value >= 50:
        score += 5
        criteria.append(
            "RSI güçlü"
        )

    if macd_ok:
        score += 5
        criteria.append(
            "MACD"
        )

    if adx_value >= 20:
        score += 5
        criteria.append(
            "ADX"
        )

    if vwap_ok:
        score += 5
        criteria.append(
            "VWAP"
        )

    score = min(
        100,
        score,
    )

    if (
        above_cloud
        and fib_poc
        and td >= 9
    ):
        status = "VERY"

    elif (
        above_cloud
        and fib_zone
        and volume_ratio_value >= 2
        and score >= 60
    ):
        status = "BUY"

    elif score >= 70:
        status = "BUY"

    else:
        status = "PASS"

    stop = (
        fib786
        if fib786 > 0
        else price * 0.99
    )

    stop_distance = (
        abs(price - stop)
        / price
        * 100
    )

    return {
        "symbol": symbol,
        "status": status,
        "price": price,
        "score": score,

        "ichimoku_bullish":
            ichi_bullish,

        "above_cloud":
            above_cloud,

        "fib_0_5":
            fib50,

        "fib_0_618":
            fib618,

        "fib_0_786":
            fib786,

        "fib_zone":
            fib_zone,

        "poc":
            poc,

        "va_low":
            va_low,

        "va_high":
            va_high,

        "fib_poc":
            fib_poc,

        "td_setup":
            td,

        "volume_ratio":
            volume_ratio_value,

        "rsi":
            rsi_value,

        "adx":
            adx_value,

        "macd":
            macd_ok,

        "vwap_ok":
            vwap_ok,

        "stop":
            stop,

        "stop_distance":
            stop_distance,

        "criteria":
            criteria,

        "streak":
            1,
    }


def rank_signals(
    signals,
):

    return sorted(
        signals,
        key=lambda x: (
            float(
                x.get(
                    "score",
                    0,
                )
            ),
            float(
                x.get(
                    "streak",
                    0,
                )
            ),
        ),
        reverse=True,
    )
