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


from kivrim import analyze_kivrim


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
        return client.klines(symbol, "5m", limit)
    except Exception:
        try:
            return client.klines(
                symbol=symbol,
                interval="5m",
                limit=limit,
            )
        except Exception:
            return []


def parse_data(data):
    highs = []
    lows = []
    closes = []
    volumes = []

    for row in data:
        try:
            if isinstance(row, dict):
                highs.append(
                    num(row.get("high", row.get("h", 0)))
                )
                lows.append(
                    num(row.get("low", row.get("l", 0)))
                )
                closes.append(
                    num(row.get("close", row.get("c", 0)))
                )
                volumes.append(
                    num(row.get("volume", row.get("v", 0)))
                )
            else:
                highs.append(num(row[2]))
                lows.append(num(row[3]))
                closes.append(num(row[4]))
                volumes.append(num(row[5]))
        except Exception:
            continue

    return highs, lows, closes, volumes


def safe_rsi(values):
    try:
        return num(rsi(values))
    except Exception:
        return 50.0


def get_streak(dbs, symbol):
    try:
        old = dbs.get_last_signal(symbol)

        if not old:
            return 1

        old_streak = int(
            old.get("streak", 0) or 0
        )

        return min(old_streak + 1, 9)

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
        item.get("symbol", "")
    ).upper()

    if not symbol or symbol in BAD_SYMBOLS:
        return None

    limit = int(
        getattr(cfg, "candles", 300)
    )

    data = get_klines(
        client,
        symbol,
        limit,
    )

    if not data:
        return None

    highs, lows, closes, volumes = parse_data(data)

    if len(closes) < 150:
        return None

    price = closes[-1]

    if price <= 0:
        return None

    # =========================================================
    # V28 KIVRIM MOTORU
    # Dip -> satÄ±ÅŸ zayÄ±flamasÄ± -> EMA7 kÄ±vrÄ±mÄ± -> ilk hareket
    # =========================================================

    kivrim_data = analyze_kivrim(
        highs,
        lows,
        closes,
        volumes,
    )

    kivrim_score = int(
        kivrim_data.get("score", 0) or 0
    )

    kivrim_early_score = int(
        kivrim_data.get("early_score", 0) or 0
    )

    kivrim_stage = str(
        kivrim_data.get(
            "stage",
            "BEKLE",
        )
    )

    kivrim_reasons = kivrim_data.get(
        "reasons",
        [],
    )

    kivrim_turning = bool(
        kivrim_data.get(
            "ema7_turning",
            False,
        )
    )

    kivrim_pre_turn = bool(
        kivrim_data.get(
            "ema7_pre_turn",
            False,
        )
    )

    kivrim_accelerating = bool(
        kivrim_data.get(
            "ema7_accelerating",
            False,
        )
    )

    kivrim_higher_low = bool(
        kivrim_data.get(
            "higher_low",
            False,
        )
    )

    kivrim_core = (
        kivrim_stage in (
            "KIVRIM Ã–NCÃœ",
            "GÃœÃ‡LENEN KIVRIM",
            "TEYÄ°T",
        )
    )

    # =========================================================
    # ICHIMOKU
    # =========================================================

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
        ichi.get("bullish", False)
    )

    above_cloud = bool(
        ichi.get("above_cloud", False)
    )

    # =========================================================
    # FIBONACCI
    # =========================================================

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

    nearest_fib = 0.0
    fib_distance = 999.0

    if fib_levels:
        nearest_fib = min(
            fib_levels,
            key=lambda x: abs(price - x),
        )

        fib_distance = fibonacci_distance(
            price,
            nearest_fib,
        )

    fib_zone = fib_distance <= 1.0

    fib618_near = (
        fib618 > 0
        and abs(price - fib618)
        / price
        * 100
        <= 1.0
    )

    fib786_near = (
        fib786 > 0
        and abs(price - fib786)
        / price
        * 100
        <= 1.0
    )

    # =========================================================
    # VOLUME PROFILE
    # =========================================================

    profile = volume_profile(
        highs,
        lows,
        closes,
        volumes,
        50,
        70,
    )

    poc = num(
        profile.get("poc", 0)
    )

    va_low = num(
        profile.get("value_low", 0)
    )

    va_high = num(
        profile.get("value_high", 0)
    )

    poc_distance = 999.0

    if poc > 0:
        poc_distance = (
            abs(price - poc)
            / price
            * 100
        )

    poc_near = poc_distance <= 1.0

    fib_poc = (
        fib_zone
        and poc_near
    )

    # =========================================================
    # TD SEQUENTIAL
    # =========================================================

    td_data = td_sequential(closes)

    td = int(
        td_data.get("setup", 0)
        or 0
    )

    td_direction = td_data.get(
        "direction",
        "",
    )

    td_9 = td >= 9
    td_13 = td >= 13

    # =========================================================
    # HACÄ°M
    # =========================================================

    vr = num(
        volume_ratio(volumes)
    )

    if len(volumes) >= 6:
        previous_volume = (
            sum(volumes[-6:-1]) / 5
        )
    else:
        previous_volume = 0.0

    impulse = (
        volumes[-1] / previous_volume
        if previous_volume > 0
        else 0.0
    )

    # =========================================================
    # RSI
    # =========================================================

    rv = safe_rsi(closes)

    rsi_previous = safe_rsi(
        closes[:-1]
    )

    rsi_rising = (
        rv > rsi_previous
    )

    # Erken hareket bÃ¶lgesi.
    rsi_early = (
        45 <= rv <= 62
    )

    # 70 Ã¼zeri artÄ±k kovalanmayacak bÃ¶lge.
    rsi_extended = rv >= 70

    # =========================================================
    # MACD
    # =========================================================

    macd_data = macd(closes)

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
        macd_line > macd_signal
    )

    # =========================================================
    # MACD DÃ–NÃœÅÃœ
    # =========================================================

    macd_turn = False

    if len(closes) >= 5:
        try:
            old_macd = macd(
                closes[:-3]
            )

            old_line = num(
                old_macd[0]
            )

            old_signal = num(
                old_macd[1]
            )

            macd_turn = (
                macd_line > macd_signal
                and old_line <= old_signal
            )
        except Exception:
            macd_turn = False

    # =========================================================
    # ADX
    # =========================================================

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
        ad_value >= 18
    )

    di_ok = (
        plus_di > minus_di
    )

    # =========================================================
    # VWAP
    # =========================================================

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

    # =========================================================
    # VWAP GERÄ° ALIM
    # =========================================================

    vwap_reclaim = False

    if (
        vwap_value > 0
        and len(closes) >= 3
    ):
        vwap_reclaim = (
            closes[-2] < vwap_value
            and closes[-1] >= vwap_value
        )

    # =========================================================
    # SIKIÅMA / EARLY BREAKOUT
    # =========================================================

    squeeze = False
    breakout = False
    early_breakout = False

    if len(closes) >= 25:
        recent_high = max(
            highs[-20:-1]
        )

        recent_low = min(
            lows[-20:-1]
        )

        range_percent = (
            (recent_high - recent_low)
            / price
            * 100
        )

        squeeze = (
            range_percent <= 6.0
        )

        breakout = (
            price > recent_high
        )

        previous_high = max(
            highs[-5:-1]
        )

        early_breakout = (
            price > previous_high
            and not rsi_extended
        )

    # =========================================================
    # Ä°LK HACÄ°M Ä°VME
    # =========================================================

    volume_start = (
        1.15 <= vr <= 3.5
    )

    volume_strong = (
        1.5 <= vr <= 3.0
    )

    # =========================================================
    # HAREKETÄ°N UZAKLIÄI
    # =========================================================

    high_20 = max(
        highs[-20:]
    )

    distance_from_high = (
        high_20 - price
    ) / price * 100

    not_extended = (
        distance_from_high >= 1.0
    )

    # Son 3 mumdaki hareket.
    if len(closes) >= 4:
        move_3 = (
            closes[-1]
            - closes[-4]
        ) / closes[-4] * 100
    else:
        move_3 = 0.0

    # Ã‡ok hÄ±zlÄ± yÃ¼kselmiÅŸse artÄ±k takip etmiyoruz.
    chase_risk = (
        move_3 >= 6.0
        or rv >= 70
    )

    # =========================================================
    # ERKEN HAREKET PROFÄ°LÄ°
    #
    # TREE Ã¶rneÄŸindeki gibi:
    # sÄ±kÄ±ÅŸma -> ilk hacim -> VWAP -> momentum -> breakout
    # =========================================================

    early_profile_score = 0

    early_profile = []

    if squeeze:
        early_profile_score += 15
        early_profile.append(
            "SÄ±kÄ±ÅŸma"
        )

    if volume_start:
        early_profile_score += 10
        early_profile.append(
            "Ä°lk hacim"
        )

    if volume_strong:
        early_profile_score += 5

    if price_above_vwap:
        early_profile_score += 10
        early_profile.append(
