from indicators import (
    adx, fibonacci, fibonacci_distance, ichimoku,
    macd, rsi, td_sequential, volume_profile,
    volume_ratio, vwap,
)
from kivrim import analyze_kivrim


BAD_SYMBOLS = {
    "USDTTRY", "USDCTRY", "BUSDTRY",
    "FDUSDTRY", "TUSDTRY", "DAITRY",
}


def num(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


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
    h, l, c, v = [], [], [], []

    for x in data:
        try:
            if isinstance(x, dict):
                h.append(num(x.get("high", x.get("h", 0))))
                l.append(num(x.get("low", x.get("l", 0))))
                c.append(num(x.get("close", x.get("c", 0))))
                v.append(num(x.get("volume", x.get("v", 0))))
            else:
                h.append(num(x[2]))
                l.append(num(x[3]))
                c.append(num(x[4]))
                v.append(num(x[5]))
        except Exception:
            continue

    return h, l, c, v


def safe_rsi(values):
    try:
        return num(rsi(values), 50)
    except Exception:
        return 50.0


def get_streak(dbs, symbol):
    try:
        old = dbs.get_last_signal(symbol)
        if not old:
            return 1
        return min(
            int(old.get("streak", 0) or 0) + 1,
            9,
        )
    except Exception:
        return 1


def analyze(cfg, client, dbs, market, item):

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

    # =====================================================
    # V29 KIVRIM
    # =====================================================

    k = analyze_kivrim(
        highs,
        lows,
        closes,
        volumes,
    )

    if not k.get("valid", False):
        return None

    kscore = int(
        k.get("score", 0) or 0
    )

    early = int(
        k.get("early_score", 0) or 0
    )

    stage = str(
        k.get("stage", "BEKLE")
    )

    k_turn = bool(
        k.get("ema7_turning", False)
    )

    k_pre = bool(
        k.get("ema7_pre_turn", False)
    )

    k_acc = bool(
        k.get("ema7_accelerating", False)
    )

    k_hl = bool(
        k.get("higher_low", False)
    )

    k_rsi = bool(
        k.get("rsi_turning", False)
    )

    k_macd = bool(
        k.get("macd_recovering", False)
    )

    k_vol = bool(
        k.get("volume_start", False)
    )

    k_reclaim = bool(
        k.get("reclaim_ema7", False)
    )

    # =====================================================
    # RSI
    # =====================================================

    rv = safe_rsi(closes)

    rv_prev = safe_rsi(
        closes[:-1]
    )

    rsi_rising = rv > rv_prev

    rsi_early = (
        35 <= rv <= 60
    )

    rsi_extended = rv >= 65

    # =====================================================
    # HACİM
    # =====================================================

    try:
        vr = num(
            volume_ratio(volumes)
        )
    except Exception:
        vr = 0.0

    volume_start = (
        1.20 <= vr <= 3.00
    )

    volume_strong = (
        1.50 <= vr <= 3.00
    )

    # =====================================================
    # MACD
    # =====================================================

    try:
        md = macd(closes)
    except Exception:
        md = ()

    ml = 0.0
    ms = 0.0
    mh = 0.0

    if isinstance(md, (tuple, list)):

        if len(md) >= 1:
            ml = num(md[0])

        if len(md) >= 2:
            ms = num(md[1])

        if len(md) >= 3:
            mh = num(md[2])

    macd_ok = ml > ms

    # =====================================================
    # ADX
    # =====================================================

    try:
        ad = adx(
            highs,
            lows,
            closes,
        )
    except Exception:
        ad = ()

    ad_value = 0.0
    plus_di = 0.0
    minus_di = 0.0

    if isinstance(ad, (tuple, list)):

        if len(ad) >= 1:
            ad_value = num(ad[0])

        if len(ad) >= 2:
            plus_di = num(ad[1])

        if len(ad) >= 3:
            minus_di = num(ad[2])

    adx_ok = ad_value >= 18
    di_ok = plus_di > minus_di

    # =====================================================
    # VWAP
    # =====================================================

    try:
        vw = num(
            vwap(
                highs,
                lows,
                closes,
                volumes,
            )
        )
    except Exception:
        vw = 0.0

    price_above_vwap = (
        vw > 0
        and price >= vw
    )

    vwap_reclaim = (
        vw > 0
        and closes[-2] < vw
        and closes[-1] >= vw
    )

    # =====================================================
    # FIB
    # =====================================================

    try:
        fib = fibonacci(
            highs,
            lows,
            closes,
        )
    except Exception:
        fib = {}

    fib50 = num(
        fib.get("0.5", 0)
    )

    fib618 = num(
        fib.get("0.618", 0)
    )

    fib786 = num(
        fib.get("0.786", 0)
    )

    levels = [
        x for x in (
            fib50,
            fib618,
            fib786,
        )
        if x > 0
    ]

    nearest_fib = 0.0
    fib_dist = 999.0

    if levels:

        nearest_fib = min(
            levels,
            key=lambda x:
            abs(price - x),
        )

        try:
            fib_dist = num(
                fibonacci_distance(
                    price,
                    nearest_fib,
                ),
                999,
            )
        except Exception:
            fib_dist = 999

    fib_zone = fib_dist <= 1.0

    fib618_near = (
        fib618 > 0
        and abs(price - fib618)
        / price * 100 <= 1
    )

    fib786_near = (
        fib786 > 0
        and abs(price - fib786)
        / price * 100 <= 1
    )

    # =====================================================
    # VOLUME PROFILE
    # =====================================================

    try:
        vp = volume_profile(
            highs,
            lows,
            closes,
            volumes,
            50,
            70,
        )
    except Exception:
        vp = {}

    poc = num(
        vp.get("poc", 0)
    )

    va_low = num(
        vp.get("value_low", 0)
    )

    va_high = num(
        vp.get("value_high", 0)
    )

    poc_near = (
        poc > 0
        and abs(price - poc)
        / price * 100 <= 1
    )

    fib_poc = (
        fib_zone
        and poc_near
    )

    # =====================================================
    # ICHIMOKU
    # =====================================================

    try:
        ichi = ichimoku(
            highs,
            lows,
            closes,
            20,
            60,
            120,
            30,
        )
    except Exception:
        ichi = {}

    ich_bull = bool(
        ichi.get("bullish", False)
    )

    above_cloud = bool(
        ichi.get("above_cloud", False)
    )

    # =====================================================
    # TD
    # =====================================================

    try:
        td_data = td_sequential(
            closes
        )
    except Exception:
        td_data = {}

    td = int(
        td_data.get("setup", 0) or 0
    )

    td_direction = td_data.get(
        "direction",
        "",
    )

    # =====================================================
    # SON HAREKET
    # =====================================================

    move3 = (
        (price - closes[-4])
        / closes[-4]
        * 100
    )

    # 4% üzeri hareketi kovalamıyoruz.
    chase = (
        move3 >= 4.0
        or rv >= 65
    )

    # =====================================================
    # SIKI ERKEN KIVRIM
    # =====================================================

    momentum_ok = (
        k_rsi
        or k_macd
    )

    structure_ok = (
        k_hl
        or k_reclaim
    )

    early_structure = (
        (k_pre or k_turn)
        and momentum_ok
        and k_vol
        and structure_ok
        and not chase
    )

    # =====================================================
    # SKOR
    # =====================================================

    score = 0
    criteria = []

    def add(condition, points, text):

        nonlocal score

        if condition:
            score += points
            criteria.append(text)

    # KIVRIM
    add(k_pre, 15, "PRE-KIVRIM")
    add(k_turn, 22, "EMA7 KIVRIM")
    add(k_acc, 6, "KIVRIM İVMESİ")
    add(k_hl, 9, "HIGHER-LOW")
    add(k_rsi, 8, "RSI DÖNÜŞÜ")
    add(k_macd, 8, "MACD DÖNÜŞÜ")
    add(k_vol, 9, "İLK HACİM")
    add(k_reclaim, 8, "EMA7 GERİ ALIM")

    # Erken yapı bonusu
    if early_structure:
        score += 7
        criteria.append(
            "1-3 MUM ERKEN YAPI"
        )

    # RSI
    add(rsi_rising, 3, "RSI YÜKSELİYOR")
    add(rsi_early, 3, "RSI ERKEN BÖLGE")

    # MACD
    add(macd_ok, 5, "MACD POZİTİF")

    # ADX
    add(adx_ok, 3, "ADX")
    add(di_ok, 3, "+DI")

    # VWAP
    add(price_above_vwap, 4, "VWAP")
    add(vwap_reclaim, 5, "VWAP GERİ ALIM")

    # HACİM
    add(volume_start, 5, "HACİM BAŞLANGICI")
    add(volume_strong, 3, "GÜÇLÜ HACİM")

    # FIB
    add(fib_zone, 3, "FIB BÖLGESİ")
    add(fib618_near, 2, "FIB 0.618")
    add(fib786_near, 2, "FIB 0.786")

    # PROFILE
    add(poc_near, 3, "POC")
    add(fib_poc, 5, "FIB + POC")

    # ICHIMOKU
    add(ich_bull, 2, "ICHIMOKU")
    add(above_cloud, 2, "BULUT ÜSTÜ")

    # TD
    if td >= 13:
        score += 3
        criteria.append("TD 13")
    elif td >= 9:
        score += 2
        criteria.append("TD 9")

    # =====================================================
    # GEÇ KALMA CEZASI
    # =====================================================

    penalty = 0

    if rv >= 60:
        penalty += 3

    if rv >= 65:
        penalty += 12

    if move3 >= 3:
        penalty += 4

    if move3 >= 4:
        penalty += 12

    score -= penalty

    score = max(
        0,
        min(100, score),
    )

    # =====================================================
    # EARLY SCORE
    # =====================================================

    early_score = early

    if early_structure:
        early_score += 5

    if chase:
        early_score -= 15

    early_score = max(
        0,
        min(100, early_score),
    )

    # =====================================================
    # SIKI STATUS
    # =====================================================

    if chase:

        status = "PASS"

    elif (
        early_score >= 68
        and k_turn
        and momentum_ok
        and k_vol
        and structure_ok
        and score >= 64
    ):

        status = "VERY"

    elif (
        early_score >= 62
        and (k_turn or k_pre)
        and momentum_ok
        and k_vol
        and structure_ok
        and score >= 60
    ):

        status = "BUY"

    elif (
        early_score >= 58
        and k_pre
        and momentum_ok
        and k_vol
        and score >= 56
    ):

        status = "ONCU"

    else:

        status = "PASS"

    # =====================================================
    # STOP
    # =====================================================

    recent_low = min(
        lows[-12:]
    )

    stop = recent_low * 0.995

    if stop <= 0 or stop >= price:
        stop = price * 0.98

    stop_distance = (
        (price - stop)
        / price
        * 100
    )

    streak = get_streak(
        dbs,
        symbol,
    )

    return {
        "symbol": symbol,
        "price": price,

        "status": status,
        "score": score,
        "priority": score,
        "streak": streak,

        # KIVRIM
        "kivrim_score": kscore,
        "kivrim_early_score": early_score,
        "kivrim_stage": stage,

        "kivrim_turning": k_turn,
        "kivrim_pre_turn": k_pre,
        "kivrim_accelerating": k_acc,
        "kivrim_higher_low": k_hl,

        "kivrim_rsi_turning": k_rsi,
        "kivrim_macd_recovering": k_macd,
        "kivrim_volume_start": k_vol,
        "kivrim_reclaim_ema7": k_reclaim,

        "kivrim_reasons": k.get(
            "reasons", []
        ),

        "kivrim_reasons_text": k.get(
            "reasons_text", ""
        ),

        # ICHIMOKU
        "ichimoku_bullish": ich_bull,
        "bullish": ich_bull,
        "above_cloud": above_cloud,

        # FIB
        "fib_0_5": fib50,
        "fib_0_618": fib618,
        "fib_0_786": fib786,

        "fib50": fib50,
        "fib618": fib618,
        "fib786": fib786,
        "fib_zone": fib_zone,
        "fib_poc": fib_poc,

        # PROFILE
        "poc": poc,
        "value_low": va_low,
        "value_high": va_high,
        "va_low": va_low,
        "va_high": va_high,

        # TD
        "td": td,
        "td_setup": td,
        "td_direction": td_direction,

        # INDICATORS
        "volume_ratio": vr,
        "vr": vr,
        "rsi": rv,
        "rv": rv,
        "macd": macd_ok,
        "macd_hist": mh,
        "adx": ad_value,
        "ad": ad_value,
        "price_above_vwap": price_above_vwap,
        "vwap": vw,

        # RISK
        "stop_loss": stop,
        "stop": stop,
        "stop_distance": stop_distance,
        "trap": chase,

        "criteria": criteria,
        "criteria_list": criteria,
    }


def rank_signals(signals, cfg=None):

    order = {
        "VERY": 3,
        "BUY": 2,
        "ONCU": 1,
        "PASS": 0,
    }

    return sorted(
        signals,
        key=lambda x: (
            order.get(
                str(x.get("status", "PASS")),
                0,
            ),
            num(
                x.get(
                    "kivrim_early_score",
                    0,
                )
            ),
            num(
                x.get(
                    "kivrim_score",
                    0,
                )
            ),
            num(
                x.get("score", 0)
            ),
        ),
        reverse=True,
    )
