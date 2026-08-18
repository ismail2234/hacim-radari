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

if symbol in {
    "USDTTRY",
    "USDCTRY",
    "BUSDTRY",
    "FDUSDTRY",
    "TUSDTRY",
    "DAITRY",
}:
    return None

    data = client.klines(
        symbol,
        "5m",
        cfg.candles,
    )

    if len(data) < 150:
        return None

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

    td = td_sequential(closes)

    rsi_value = rsi(closes)

    macd_line, macd_signal, macd_hist = macd(
        closes
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

    vr = volume_ratio(volumes)

    fib050 = fib.get("0.5", 0)
    fib618 = fib.get("0.618", 0)
    fib786 = fib.get("0.786", 0)

    poc = profile.get("poc", 0)

    fib_zone = False

    if fib618 > 0:
        if (
            abs(price - fib618)
            / price
            * 100
            <= cfg.fib_tolerance
        ):
            fib_zone = True

    if fib786 > 0:
        if (
            abs(price - fib786)
            / price
            * 100
            <= cfg.fib_tolerance
        ):
            fib_zone = True

    fib_poc = False

    if fib618 > 0 and poc > 0:
        if (
            abs(fib618 - poc)
            / price
            * 100
            <= cfg.fib_tolerance
        ):
            fib_poc = True

    if fib786 > 0 and poc > 0:
        if (
            abs(fib786 - poc)
            / price
            * 100
            <= cfg.fib_tolerance
        ):
            fib_poc = True

    td_count = int(
        td.get("setup", 0)
        or 0
    )

    td_direction = td.get(
        "direction",
        "",
    )

    td9 = (
        td_count >= cfg.td_buy
        and td_direction == "down"
    )

    td13 = (
        td_count >= cfg.td_strong
        and td_direction == "down"
    )

    trend_ok = (
        bool(ichi.get("above_cloud"))
        and bool(ichi.get("bullish"))
    )

    macd_ok = (
        macd_line > macd_signal
        or macd_hist > 0
    )

    adx_ok = (
        adx_value > 0
        and adx_value >= 20
        and plus_di > minus_di
    )

    vwap_ok = (
        vwap_value > 0
        and price > vwap_value
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

    if td9:
        score += 20

    if td13:
        score += 5

    if volume_ok:
        score += 5

    if macd_ok:
        score += 2

    if adx_ok:
        score += 2

    if vwap_ok:
        score += 1

    score = min(
        100,
        score,
    )

    strong = (
        score >= cfg.min_score_strong
        and trend_ok
        and fib_zone
        and fib_poc
        and td9
        and volume_ok
        and adx_ok
    )

    buy = (
        score >= cfg.min_score_buy
        and trend_ok
        and fib_zone
        and fib_poc
        and volume_ok
    )

    if strong:
        status = "VERY"

    elif buy:
        status = "BUY"

    else:
        status = "PASS"

    criteria = []

    if trend_ok:
        criteria.append(
            "Ichimoku"
        )

    if fib_zone:
        criteria.append(
            "Fib bölgesi"
        )

    if fib_poc:
        criteria.append(
            "Fib+POC"
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

    stop = fib786

    if stop <= 0:
        stop = price * 0.97

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

        "ichimoku_bullish":
            bool(ichi.get("bullish")),

        "above_cloud":
            bool(ichi.get("above_cloud")),

        "fib_050": fib050,
        "fib_0618": fib618,
        "fib_0786": fib786,

        "fib_zone": fib_zone,
        "poc": poc,

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

        "fib_poc": fib_poc,

        "td_setup": td_count,
        "td_direction": td_direction,
        "td9": td9,
        "td13": td13,

        "rsi": rsi_value,
        "macd": macd_ok,
        "adx": adx_value,
        "plus_di": plus_di,
        "minus_di": minus_di,

        "vwap": vwap_value,
        "vwap_ok": vwap_ok,

        "volume_ratio": vr,

        "criteria": criteria,

        "stop": stop,
        "stop_distance": stop_distance,
    }


def rank_signals(results):

    return sorted(
        results,
        key=lambda x: (
            x.get("score", 0),
            int(
                x.get("td13", False)
            ),
            int(
                x.get("td9", False)
            ),
            int(
                x.get("fib_poc", False)
            ),
        ),
        reverse=True,
    )
