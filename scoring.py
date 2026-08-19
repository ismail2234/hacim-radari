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
    # HACİM
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

    # Erken hareket bölgesi.
    rsi_early = (
        45 <= rv <= 62
    )

    # 70 üzeri artık kovalanmayacak bölge.
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
    # MACD DÖNÜŞÜ
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
    # VWAP GERİ ALIM
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
    # SIKIŞMA / EARLY BREAKOUT
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
    # İLK HACİM İVME
    # =========================================================

    volume_start = (
        1.15 <= vr <= 3.5
    )

    volume_strong = (
        1.5 <= vr <= 3.0
    )

    # =========================================================
    # HAREKETİN UZAKLIĞI
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

    # Çok hızlı yükselmişse artık takip etmiyoruz.
    chase_risk = (
        move_3 >= 6.0
        or rv >= 70
    )

    # =========================================================
    # ERKEN HAREKET PROFİLİ
    #
    # TREE örneğindeki gibi:
    # sıkışma -> ilk hacim -> VWAP -> momentum -> breakout
    # =========================================================

    early_profile_score = 0

    early_profile = []

    if squeeze:
        early_profile_score += 15
        early_profile.append(
            "Sıkışma"
        )

    if volume_start:
        early_profile_score += 10
        early_profile.append(
            "İlk hacim"
        )

    if volume_strong:
        early_profile_score += 5

    if price_above_vwap:
        early_profile_score += 10
        early_profile.append(
            "VWAP"
        )

    if vwap_reclaim:
        early_profile_score += 12
        early_profile.append(
            "VWAP geri alım"
        )

    if macd_turn:
        early_profile_score += 12
        early_profile.append(
            "MACD dönüş"
        )

    if macd_ok:
        early_profile_score += 6
        early_profile.append(
            "MACD"
        )

    if rsi_early:
        early_profile_score += 10
        early_profile.append(
            "RSI erken"
        )

    if rsi_rising:
        early_profile_score += 5
        early_profile.append(
            "RSI yükseliyor"
        )

    if early_breakout:
        early_profile_score += 12
        early_profile.append(
            "İlk kırılma"
        )

    if breakout:
        early_profile_score += 5

    if not_extended:
        early_profile_score += 8
        early_profile.append(
            "Hareket uzamamış"
        )

    early_profile_score = min(
        early_profile_score,
        100,
    )

    # =========================================================
    # TEKNİK SKOR
    # =========================================================

    score = 0
    criteria = []

    if ichimoku_bullish:
        score += 12
        criteria.append(
            "Ichimoku yükseliş"
        )

    if above_cloud:
        score += 6
        criteria.append(
            "Bulut üstü"
        )

    if fib786_near:
        score += 12
        criteria.append(
            "Fib 0.786"
        )

    elif fib618_near:
        score += 10
        criteria.append(
            "Fib 0.618"
        )

    elif fib_zone:
        score += 6
        criteria.append(
            "Fib bölgesi"
        )

    if poc_near:
        score += 10
        criteria.append(
            "POC yakın"
        )

    if fib_poc:
        score += 12
        criteria.append(
            "Fib+POC kesişimi"
        )

    if volume_start:
        score += 8
        criteria.append(
            f"Hacim {vr:.1f}x"
        )

    if rsi_early:
        score += 8
        criteria.append(
            "RSI erken"
        )

    elif 45 <= rv <= 65:
        score += 4
        criteria.append(
            "RSI"
        )

    if rsi_rising:
        score += 4
        criteria.append(
            "RSI yükseliyor"
        )

    if macd_ok:
        score += 7
        criteria.append(
            "MACD"
        )

    if macd_turn:
        score += 5
        criteria.append(
            "MACD dönüş"
        )

    if adx_ok:
        score += 5
        criteria.append(
            "ADX"
        )

    if di_ok:
        score += 5
        criteria.append(
            "+DI"
        )

    if price_above_vwap:
        score += 5
        criteria.append(
            "VWAP"
        )

    if squeeze:
        score += 5
        criteria.append(
            "Sıkışma"
        )

    if early_breakout:
        score += 7
        criteria.append(
            "İlk kırılma"
        )

    if td_9:
        score += 2
        criteria.append(
            "TD9"
        )

    if td_13:
        score += 2
        criteria.append(
            "TD13"
        )

    score = min(
        score,
        100,
    )

    # =========================================================
    # ERKENLİK PUANI
    # =========================================================

    early_score = early_profile_score

    if fib_poc:
        early_score += 8

    if fib786_near:
        early_score += 8

    elif fib618_near:
        early_score += 6

    if poc_near:
        early_score += 5

    if ichimoku_bullish:
        early_score += 4

    if di_ok:
        early_score += 3

    early_score = min(
        early_score,
        100,
    )

    # =========================================================
    # GEÇ KALMA CEZASI
    # =========================================================

    if rv >= 65:
        early_score -= 10

    if rv >= 70:
        early_score -= 25

    if move_3 >= 4:
        early_score -= 10

    if move_3 >= 6:
        early_score -= 25

    if distance_from_high < 1:
        early_score -= 15

    early_score = max(
        0,
        min(
            early_score,
            100,
        ),
    )

    # =========================================================
    # SİNYAL SEVİYESİ
    # =========================================================

    status = "PASS"

    early_core = (
        volume_start
        and rsi_early
        and not chase_risk
    )

    technical_core = (
        macd_ok
        and di_ok
        and price_above_vwap
    )

    if (
        early_core
        and early_profile_score >= 55
        and early_score >= 60
        and (
            fib_poc
            or
            fib_zone
            or
            poc_near
            or
            early_breakout
        )
    ):
        status = "ONCU"

    if (
        status == "ONCU"
        and technical_core
        and early_score >= 68
        and (
            fib_poc
            or
            vwap_reclaim
            or
            macd_turn
        )
    ):
        status = "BUY"

    if (
        status == "BUY"
        and early_score >= 80
        and fib_poc
        and volume_strong
        and not td_13
        and rv < 62
    ):
        status = "VERY"

    # Aşırı yükselmiş coinleri kovalamıyoruz.
    if chase_risk:
        status = "PASS"

    if rsi_extended:
        status = "PASS"

    if move_3 >= 8:
        status = "PASS"

    # =========================================================
    # STOP
    # =========================================================

    stop = fib786

    if (
        stop <= 0
        or stop >= price
    ):
        stop = price * 0.99

    stop_distance = (
        abs(price - stop)
        / price
        * 100
    )

    # =========================================================
    # TEYİT
    # =========================================================

    streak = get_streak(
        dbs,
        symbol,
    )

    # =========================================================
    # ÖNCELİK
    # =========================================================

    priority = (
        early_score * 0.60
        + score * 0.40
    )

    if fib_poc:
        priority += 5

    if macd_turn:
        priority += 5

    if vwap_reclaim:
        priority += 5

    priority = min(
        priority,
        100,
    )

    entry_quality = (
        early_score * 0.70
        + score * 0.30
    )

    entry_quality = min(
        entry_quality,
        100,
    )

    # =========================================================
    # SONUÇ
    # =========================================================

    return {
        "symbol": symbol,
        "status": status,
        "price": price,

        "score": round(score, 2),
        "early_score": round(
            early_score,
            2,
        ),
        "early_profile_score": round(
            early_profile_score,
            2,
        ),

        "priority": round(
            priority,
            2,
        ),

        "entry_quality": round(
            entry_quality,
            2,
        ),

        "ichimoku_bullish":
            ichimoku_bullish,

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

        "fib_618_near":
            fib618_near,

        "fib_786_near":
            fib786_near,

        "poc":
            poc,

        "va_low":
            va_low,

        "va_high":
            va_high,

        "poc_distance":
            poc_distance,

        "fib_poc":
            fib_poc,

        "td_setup":
            td,

        "td_direction":
            td_direction,

        "td_9":
            td_9,

        "td_13":
            td_13,

        "volume_ratio":
            vr,

        "impulse":
            impulse,

        "rsi":
            rv,

        "rsi_rising":
            rsi_rising,

        "macd":
            macd_ok,

        "macd_turn":
            macd_turn,

        "macd_line":
            macd_line,

        "macd_signal":
            macd_signal,

        "macd_hist":
            macd_hist,

        "adx":
            ad_value,

        "plus_di":
            plus_di,

        "minus_di":
            minus_di,

        "adx_ok":
            adx_ok,

        "di_ok":
            di_ok,

        "vwap":
            vwap_value,

        "price_above_vwap":
            price_above_vwap,

        "vwap_reclaim":
            vwap_reclaim,

        "squeeze":
            squeeze,

        "breakout":
            breakout,

        "early_breakout":
            early_breakout,

        "distance_from_high":
            distance_from_high,

        "move_3":
            move_3,

        "early_profile":
            early_profile,

        "early_profile_text":
            ", ".join(
                early_profile
            ),

        "stop":
            stop,

        "stop_loss":
            stop,

        "stop_distance":
            stop_distance,

        "criteria":
            criteria,

        "criteria_list":
            criteria,

        "streak":
            streak,

        "previous_signal":
            (
                "İlk sinyal"
                if streak <= 1
                else f"{streak - 1}. teyit"
            ),
    }


def rank_signals(
    signals,
    cfg=None,
):
    valid = [
        item
        for item in signals
        if item.get("status")
        in (
            "ONCU",
            "BUY",
            "VERY",
        )
    ]

    return sorted(
        valid,
        key=lambda item: (
            float(
                item.get(
                    "early_score",
                    0,
                )
            ) * 0.50
            +
            float(
                item.get(
                    "early_profile_score",
                    0,
                )
            ) * 0.25
            +
            float(
                item.get(
                    "score",
                    0,
                )
            ) * 0.15
            +
            float(
                item.get(
                    "priority",
                    0,
                )
            ) * 0.10
        ),
        reverse=True,
    )
