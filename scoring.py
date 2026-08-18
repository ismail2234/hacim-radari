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


def num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def get_klines(client, symbol, limit):
    try:
        return client.klines(
            symbol,
            "5m",
            limit,
        )
    except TypeError:
        try:
            return client.klines(
                symbol=symbol,
                interval="5m",
                limit=limit,
            )
        except Exception:
            return []
    except Exception:
        return []


def parse_data(data):
    highs = []
    lows = []
    closes = []
    volumes = []

    for row in data:

        if isinstance(row, dict):

            try:
                highs.append(
                    num(
                        row.get(
                            "high",
                            row.get("h", 0),
                        )
                    )
                )

                lows.append(
                    num(
                        row.get(
                            "low",
                            row.get("l", 0),
                        )
                    )
                )

                closes.append(
                    num(
                        row.get(
                            "close",
                            row.get("c", 0),
                        )
                    )
                )

                volumes.append(
                    num(
                        row.get(
                            "volume",
                            row.get("v", 0),
                        )
                    )
                )

            except Exception:
                continue

        else:

            try:
                highs.append(
                    num(row[2])
                )
                lows.append(
                    num(row[3])
                )
                closes.append(
                    num(row[4])
                )
                volumes.append(
                    num(row[5])
                )
            except Exception:
                continue

    return (
        highs,
        lows,
        closes,
        volumes,
    )


def get_streak(dbs, symbol):

    try:
        old = dbs.get_last_signal(
            symbol
        )

        if not old:
            return 1

        old_streak = int(
            old.get(
                "streak",
                0,
            ) or 0
        )

        return min(
            old_streak + 1,
            9,
        )

    except Exception:
        return 1


def analyze(
    cfg,
    client,
    dbs,
    market,
    item,
):

    symbol = str(
        item.get(
            "symbol",
            "",
        )
    ).upper()

    if not symbol:
        return None

    if symbol in BAD_SYMBOLS:
        return None

    limit = int(
        getattr(
            cfg,
            "candles",
            250,
        )
    )

    data = get_klines(
        client,
        symbol,
        limit,
    )

    if not data:
        return None

    highs, lows, closes, volumes = (
        parse_data(data)
    )

    if len(closes) < 150:
        return None

    price = closes[-1]

    if price <= 0:
        return None

    # -----------------------------
    # ICHIMOKU
    # -----------------------------

    ichi = ichimoku(
        highs,
        lows,
        closes,
        20,
        60,
        120,
        30,
    )

    ichimoku_bullish = bool(
        ichi.get(
            "bullish",
            False,
        )
    )

    above_cloud = bool(
        ichi.get(
            "above_cloud",
            False,
        )
    )

    # -----------------------------
    # FIBONACCI
    # -----------------------------

    fib = fibonacci(
        highs,
        lows,
        closes,
    )

    fib50 = num(
        fib.get("0.5", 0)
    )

    fib618 = num(
        fib.get("0.618", 0)
    )

    fib786 = num(
        fib.get("0.786", 0)
    )

    fib_levels = [
        x
        for x in (
            fib50,
            fib618,
            fib786,
        )
        if x > 0
    ]

    fib_distance = 999.0

    nearest_fib = 0.0

    if fib_levels:

        nearest_fib = min(
            fib_levels,
            key=lambda x:
            abs(price - x),
        )

        fib_distance = (
            fibonacci_distance(
                price,
                nearest_fib,
            )
        )

    fib_zone = (
        fib_distance <= 1.0
    )

    # -----------------------------
    # VOLUME PROFILE
    # -----------------------------

    profile = volume_profile(
        highs,
        lows,
        closes,
        volumes,
        50,
        70,
    )

    poc = num(
        profile.get(
            "poc",
            0,
        )
    )

    va_low = num(
        profile.get(
            "value_low",
            0,
        )
    )

    va_high = num(
        profile.get(
            "value_high",
            0,
        )
    )

    poc_distance = 999.0

    if poc > 0:
        poc_distance = (
            abs(price - poc)
            / price
            * 100
        )

    poc_near = (
        poc_distance <= 1.0
    )

    fib_poc = (
        fib_zone
        and poc_near
    )

    # -----------------------------
    # TD SEQUENTIAL
    # -----------------------------

    td_data = td_sequential(
        closes
    )

    td = int(
        td_data.get(
            "setup",
            0,
        )
        or 0
    )

    td_direction = td_data.get(
        "direction",
        "",
    )

    td_9 = td >= 9
    td_13 = td >= 13

    # -----------------------------
    # HACİM
    # -----------------------------

    vr = volume_ratio(
        volumes
    )

    vr = num(vr)

    # -----------------------------
    # RSI
    # -----------------------------

    rv = num(
        rsi(
            closes
        )
    )

    # -----------------------------
    # MACD
    # -----------------------------

    macd_data = macd(
        closes
    )

    macd_line = 0.0
    macd_signal = 0.0
    macd_hist = 0.0

    if isinstance(
        macd_data,
        (tuple, list),
    ):

        if len(macd_data) >= 1:
            macd_line = num(
                macd_data[0]
            )

        if len(macd_data) >= 2:
            macd_signal = num(
                macd_data[1]
            )

        if len(macd_data) >= 3:
            macd_hist = num(
                macd_data[2]
            )

    macd_ok = (
        macd_line
        > macd_signal
    )

    # -----------------------------
    # ADX
    # -----------------------------

    adx_data = adx(
        highs,
        lows,
        closes,
    )

    ad_value = 0.0
    plus_di = 0.0
    minus_di = 0.0

    if isinstance(
        adx_data,
        (tuple, list),
    ):

        if len(adx_data) >= 1:
            ad_value = num(
                adx_data[0]
            )

        if len(adx_data) >= 2:
            plus_di = num(
                adx_data[1]
            )

        if len(adx_data) >= 3:
            minus_di = num(
                adx_data[2]
            )

    adx_ok = (
        ad_value >= 20
    )

    di_ok = (
        plus_di > minus_di
    )

    # -----------------------------
    # VWAP
    # -----------------------------

    vwap_value = num(
        vwap(
            highs,
            lows,
            closes,
            volumes,
        )
    )

    price_above_vwap = (
        vwap_value > 0
        and price >= vwap_value
    )

    # -----------------------------
    # HACİM İVME
    # -----------------------------

    previous_volume = 0.0

    if len(volumes) >= 6:

        previous_volume = (
            sum(
                volumes[-6:-1]
            )
            / 5
        )

    impulse = (
        volumes[-1]
        / previous_volume
        if previous_volume > 0
        else 0.0
    )

    # -----------------------------
    # RSI YÖNÜ
    # -----------------------------

    rsi_previous = num(
        rsi(
            closes[:-1]
        )
    )

    rsi_rising = (
        rv > rsi_previous
    )

    # -----------------------------
    # SKOR
    # -----------------------------

    score = 0

    criteria = []

    if ichimoku_bullish:

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

    if vr >= 2:

        score += 10

        criteria.append(
            f"Hacim {vr:.1f}x"
        )

    if rv >= 50:

        score += 5

        criteria.append(
            "RSI güçlü"
        )

    if rsi_rising:

        score += 5

        criteria.append(
            "RSI yükseliyor"
        )

    if macd_ok:

        score += 5

        criteria.append(
            "MACD"
        )

    if adx_ok:

        score += 5

        criteria.append(
            "ADX"
        )

    if di_ok:

        score += 5

        criteria.append(
            "ADX/+DI"
        )

    if price_above_vwap:

        score += 5

        criteria.append(
            "VWAP"
        )

    score = min(
        score,
        100,
    )
