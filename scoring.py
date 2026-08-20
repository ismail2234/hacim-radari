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


def safe(fn, *args, default=None):
    try:
        return fn(*args)
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
    return num(safe(rsi, values, default=50.0), 50.0)


def get_streak(dbs, symbol):
    try:
        old = dbs.get_last_signal(symbol)
        if not old:
            return 1
        return min(int(old.get("streak", 0) or 0) + 1, 9)
    except Exception:
        return 1


def analyze(cfg, client, dbs, market, item):
    symbol = str(item.get("symbol", "")).upper()

    if not symbol or symbol in BAD_SYMBOLS:
        return None

    limit = int(getattr(cfg, "candles", 300))
    data = get_klines(client, symbol, limit)

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

    kscore = int(k.get("score", 0) or 0)
    early = int(k.get("early_score", 0) or 0)
    stage = str(k.get("stage", "BEKLE"))

    k_turn = bool(k.get("ema7_turning", False))
    k_pre = bool(k.get("ema7_pre_turn", False))
    k_acc = bool(k.get("ema7_accelerating", False))
    k_hl = bool(k.get("higher_low", False))
    k_rsi = bool(k.get("rsi_turning", False))
    k_macd = bool(k.get("macd_recovering", False))
    k_vol = bool(k.get("volume_start", False))
    k_reclaim = bool(k.get("reclaim_ema7", False))

    # =====================================================
    # ICHIMOKU
    # =====================================================

    ichi = safe(
        ichimoku,
        highs, lows, closes,
        20, 60, 120, 30,
        default={},
    ) or {}

    ich_bull = bool(ichi.get("bullish", False))
    above_cloud = bool(ichi.get("above_cloud", False))

    # =====================================================
    # FIBONACCI
    # =====================================================

    fib = safe(
        fibonacci,
        highs, lows, closes,
        default={},
    ) or {}

    fib50 = num(fib.get("0.5", 0))
    fib618 = num(fib.get("0.618", 0))
    fib786 = num(fib.get("0.786", 0))

    levels = [
        x for x in
        (fib50, fib618, fib786)
        if x > 0
    ]

    nearest_fib = 0
    fib_distance = 999

    if levels:
        nearest_fib = min(
            levels,
            key=lambda x: abs(price - x),
        )
        fib_distance = num(
            safe(
                fibonacci_distance,
                price,
                nearest_fib,
                default=999,
            ),
            999,
        )

    fib_zone = fib_distance <= 1.0

    fib618_near = (
        fib618 > 0
        and abs(price - fib618) / price * 100 <= 1
    )

    fib786_near = (
        fib786 > 0
        and abs(price - fib786) / price * 100 <= 1
    )

    # =====================================================
    # VOLUME PROFILE
    # =====================================================

    vp = safe(
        volume_profile,
        highs, lows, closes, volumes,
        50, 70,
        default={},
    ) or {}

    poc = num(vp.get("poc", 0))
    va_low = num(vp.get("value_low", 0))
    va_high = num(vp.get("value_high", 0))

    poc_distance = (
        abs(price - poc) / price * 100
        if poc > 0 else 999
    )

    poc_near = poc_distance <= 1
    fib_poc = fib_zone and poc_near

    # =====================================================
    # TD
    # =====================================================

    td_data = safe(
        td_sequential,
        closes,
        default={},
    ) or {}

    td = int(td_data.get("setup", 0) or 0)
    td_direction = td_data.get("direction", "")

    # =====================================================
    # HACİM
    # =====================================================

    vr = num(
        safe(
            volume_ratio,
            volumes,
            default=0,
        )
    )

    volume_start = (
        1.10 <= vr <= 3.50
    )

    volume_strong = (
        1.40 <= vr <= 3.00
    )

    # =====================================================
    # RSI
    # =====================================================

    rv = safe_rsi(closes)

    rsi_prev = safe_rsi(closes[:-1])

    rsi_rising = rv > rsi_prev
    rsi_early = 35 <= rv <= 62
    rsi_extended = rv >= 70

    # =====================================================
    # MACD
    # =====================================================

    md = safe(
        macd,
        closes,
        default=(),
    )

    ml = ms = mh = 0.0

    if isinstance(md, (list, tuple)):
        if len(md) > 0:
            ml = num(md[0])
        if len(md) > 1:
            ms = num(md[1])
        if len(md) > 2:
            mh = num(md[2])

    macd_ok = ml > ms

    # =====================================================
    # ADX
    # =====================================================

    ad = safe(
        adx,
        highs, lows, closes,
        default=(),
    )

    ad_value = plus_di = minus_di = 0.0

    if isinstance(ad, (list, tuple)):
        if len(ad) > 0:
            ad_value = num(ad[0])
        if len(ad) > 1:
            plus_di = num(ad[1])
        if len(ad) > 2:
            minus_di = num(ad[2])

    adx_ok = ad_value >= 18
    di_ok = plus_di > minus_di

    # =====================================================
    # VWAP
    # =====================================================

    vw = num(
        safe(
            vwap,
            highs, lows, closes, volumes,
            default=0,
        )
    )

    price_above_vwap = (
        vw > 0 and price >= vw
    )

    vwap_reclaim = False

    if vw > 0 and len(closes) >= 3:
        vwap_reclaim = (
            closes[-2] < vw
            and closes[-1] >= vw
        )

    # =====================================================
    # HAREKET / KOVALAMA KONTROLÜ
    # =====================================================

    move3 = (
        (price - closes[-4])
        / closes[-4] * 100
    )

    chase = (
        move3 >= 6
        or rv >= 70
    )

    # =====================================================
    # SKOR
    # =====================================================

    score = 0
    criteria = []

    def add(cond, pts, text):
        nonlocal score
        if cond:
            score += pts
            criteria.append(text)

    # --- KIVRIM ---
    add(k_pre, 18, "PRE-KIVRIM")
    add(k_turn, 20, "EMA7 KIVRIM")
    add(k_acc, 7, "KIVRIM İVMESİ")
    add(k_hl, 8, "HIGHER-LOW")
    add(k_rsi, 7, "KIVRIM RSI")
    add(k_macd, 7, "KIVRIM MACD")
    add(k_vol, 7, "KIVRIM HACİM")
    add(k_reclaim, 7, "EMA7 GERİ ALIM")

    # --- ERKEN KIVRIM BONUS ---
    early_structure = (
        (k_pre or k_turn)
        and (k_rsi or k_macd or k_vol)
        and (k_hl or k_reclaim)
    )

    if early_structure and not chase:
        score += 12
        criteria.append("1-3 MUM ERKEN ADAY")

    if early >= 60 and not chase:
        score += 8
        criteria.append("ERKENLİK GÜÇLÜ")

    # --- MOMENTUM ---
    add(rsi_rising, 4, "RSI YÜKSELİYOR")
    add(rsi_early, 4, "RSI ERKEN BÖLGE")
    add(macd_ok, 6, "MACD POZİTİF")
    add(adx_ok, 4, "ADX")
    add(di_ok, 4, "+DI")
    add(price_above_vwap, 5, "VWAP")

    # --- HACİM ---
    add(volume_start, 6, "İLK HACİM")
    add(volume_strong, 4, "GÜÇLÜ HACİM")

    # --- YAPISAL ---
    add(ich_bull, 3, "ICHIMOKU")
    add(above_cloud, 3, "BULUT ÜSTÜ")
    add(fib_zone, 4, "FIB BÖLGESİ")
    add(fib618_near, 3, "FIB 0.618")
    add(fib786_near, 3, "FIB 0.786")
    add(poc_near, 4, "POC")
    add(fib_poc, 6, "FIB + POC")
    add(vwap_reclaim, 6, "VWAP GERİ ALIM")

    # --- TD ---
    if td >= 13:
        score += 4
        criteria.append("TD 13")
    elif td >= 9:
        score += 2
        criteria.append("TD 9")

    # =====================================================
    # GEÇ KALMA CEZASI
    # =====================================================

    penalty = 0

    if rv >= 65:
        penalty += 5

    if rv >= 70:
        penalty += 15

    if move3 >= 4:
        penalty += 6

    if move3 >= 7:
        penalty += 15

    if chase:
        penalty += 10

    score -= penalty
    score = max(0, min(100, score))

    # =====================================================
    # STATUS
    # =====================================================

    if chase:
        status = "PASS"

    elif (
        early >= 65
        and early_structure
        and score >= 58
    ):
        status = "VERY"

    elif (
        early >= 52
        and early_structure
        and score >= 48
    ):
        status = "BUY"

    elif (
        k_pre
        and early >= 45
        and score >= 42
    ):
        status = "ONCU"

    elif (
        k_turn
        and score >= 48
    ):
        status = "BUY"

    else:
        status = "PASS"

    # =====================================================
    # STOP
    # =====================================================

    recent_low = min(lows[-12:])

    stop = recent_low * 0.995

    if stop <= 0 or stop >= price:
        stop = price * 0.98

    stop_distance = (
        (price - stop)
        / price * 100
    )

    # =====================================================
    # STREAK
    # =====================================================

    streak = get_streak(
        dbs,
        symbol,
    )

    # =====================================================
    # RESULT
    # =====================================================

    return {
        "symbol": symbol,
        "price": price,

        "status": status,
        "score": score,
        "priority": score,

        "streak": streak,

        # KIVRIM
        "kivrim_score": kscore,
        "kivrim_early_score": early,
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
            "reasons",
            [],
        ),

        "kivrim_reasons_text": k.get(
            "reasons_text",
            "",
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

        # CRITERIA
        "criteria": criteria,
        "criteria_list": criteria,
    }


def rank_signals(signals, cfg=None):
    def key(x):
        status = str(
            x.get("status", "PASS")
        )

        rank = {
            "VERY": 3,
            "BUY": 2,
            "ONCU": 1,
            "PASS": 0,
        }.get(status, 0)

        return (
            rank,
            num(x.get("kivrim_early_score", 0)),
            num(x.get("kivrim_score", 0)),
            num(x.get("score", 0)),
        )

    return sorted(
        signals,
        key=key,
        reverse=True,
    )
