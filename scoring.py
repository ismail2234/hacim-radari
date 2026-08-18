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


def analyze(cfg, client, symbol):

    data = client.klines(
        symbol,
        "5m",
        cfg.candles,
    )

    if len(data) < 150:
        return None

    opens = [float(x[1]) for x in data]
    highs = [float(x[2]) for x in data]
    lows = [float(x[3]) for x in data]
    closes = [float(x[4]) for x in data]
    volumes = [float(x[5]) for x in data]

    price = closes[-1]

    ichi = ichimoku(
        highs,
        lows,
        closes,
        cfg.ichimoku_conversion,
        cfg.ichimoku_base,
        cfg.ichimoku_span,
        cfg.ichimoku_displacement,
    )

    fib = fibonacci(
        highs,
        lows,
        closes,
    )

    profile = volume_profile(
        highs,
        lows,
        closes,
        volumes,
        cfg.profile_bins,
        cfg.profile_value_area,
    )

    td = td_sequential(
        closes,
    )

    rsi_value = rsi(
        closes,
    )

    macd_line, macd_signal, macd_hist = macd(
        closes,
    )

    adx_value, plus_di, minus_di = adx(
        highs,
        lows,
        closes,
    )

    vwap_value = vwap(
        highs,
        lows,
        closes,
        volumes,
    )

    vr = volume_ratio(
        volumes,
    )

    fib618 = fib.get(
        "0.618",
        0.0,
    )

    fib786 = fib.get(
        "0.786",
        0.0,
    )

    poc = profile.get(
        "poc",
        0.0,
    )

    fib618_dist = (
        fibonacci_distance(
            price,
            fib618,
        )
        if fib618 > 0
        else 999
    )

    fib786_dist = (
        fibonacci_distance(
            price,
            fib786,
        )
        if fib786 > 0
        else 999
    )

    poc_dist = (
        fibonacci_distance(
            price,
            poc,
        )
        if poc > 0
        else 999
    )

    fib_poc = (
        fib618 > 0
        and poc > 0
        and abs(fib618 - poc)
        / price
        * 100
        <= cfg.fib_tolerance
    )

    fib_zone = (
        fib618_dist <= cfg.fib_tolerance
        or fib786_dist <= cfg.fib_tolerance
    )

    td9 = td["setup"] >= 9
    td13 = td["setup"] >= 13

    td_buy = (
        td["direction"] == "down"
        and td9
    )

    trend_ok = (
        ichi["above_cloud"]
        and ichi["bullish"]
    )

    macd_ok = (
        macd_line > macd_signal
        or macd_hist > 0
    )

    adx_ok = (
        adx_value >= 20
        and plus_di > minus_di
    )

    vwap_ok = (
        price > vwap_value
        if vwap_value > 0
        else False
    )

    volume_ok = (
        vr >= cfg.volume_ratio
    )

    score = 0

    if trend_ok:
        score += 25

    if fib_zone:
        score += 20

    if fib_poc:
        score += 20

    if td_buy:
        score += 20

    if td13:
        score += 5

    if volume_ok:
        score += 5

    if macd_ok:
        score += 3

    if adx_ok:
        score += 2

    if vwap_ok:
        score += 2

    score = min(
        100,
        score,
    )

    if not trend_ok:
        status = "PASS"

    elif (
        score >= 85
        and fib_poc
        and td_buy
    ):
        status = "VERY"

    elif (
        score >= 70
        and fib_zone
    ):
        status = "BUY"

    else:
        status = "PASS"

    if (
        status != "PASS"
        and not trend_ok
    ):
        status = "PASS"

    criteria = []

    if trend_ok:
        criteria.append(
            "Ichimoku yükseliş"
        )

    if fib_zone:
        criteria.append(
            "Fib bölgesi"
        )

    if fib_poc:
        criteria.append(
            "Fib+POC kesişimi"
        )

    if td9:
        criteria.append(
            "TD9"
        )

    if td13:
        criteria.append(
            "TD13"
        )

    if volume_ok:
        criteria.append(
            f"Hacim {vr:.1f}x"
        )

    if macd_ok:
        criteria.append(
            "MACD"
        )

    if adx_ok:
        criteria.append(
            "ADX"
        )

    if vwap_ok:
        criteria.append(
            "VWAP"
        )

    if status == "PASS":
        streak = 0
    else:
        streak = 1

    stop = min(
        x
        for x in [
            fib786,
            profile.get(
                "value_low",
                0,
            ),
        ]
        if x > 0
    ) if (
        fib786 > 0
        or profile.get(
            "value_low",
            0,
        ) > 0
    ) else price * 0.97

    stop_distance = (
        abs(price - stop)
        / price
        * 100
    )

    return {
        "symbol": symbol,
        "price": price,
        "status": status,
        "score": score,

        "streak": streak,

        "ichimoku_bullish":
            ichi["bullish"],

        "above_cloud":
            ichi["above_cloud"],

        "tenkan":
            ichi["tenkan"],

        "kijun":
            ichi["kijun"],

        "fib_050":
            fib.get("0.5", 0),

        "fib_0618":
            fib618,

        "fib_0786":
            fib786,

        "fib_zone":
            fib_zone,

        "poc":
            poc,

        "value_low":
            profile.get(
                "value_low",
                0,
            ),

        "value_high":
            profile.get(
                "value_high",
                0,
            ),

        "fib_poc":
            fib_poc,

        "poc_distance":
            poc_dist,

        "td_setup":
            td["setup"],

        "td_direction":
            td["direction"],

        "td9":
            td9,

        "td13":
            td13,

        "rsi":
            rsi_value,

        "macd":
            macd_ok,

        "adx":
            adx_value,

        "plus_di":
            plus_di,

        "minus_di":
            minus_di,

        "vwap":
            vwap_value,

        "vwap_ok":
            vwap_ok,

        "volume_ratio":
            vr,

        "criteria":
            criteria,

        "stop":
            stop,

        "stop_distance":
            stop_distance,

        "market_ok":
            trend_ok,
    }


def rank_signals(results):

    return sorted(
        results,
        key=lambda x: (
            x.get("score", 0),
            x.get("fib_poc", False),
            x.get("td9", False),
        ),
        reverse=True,
    )
