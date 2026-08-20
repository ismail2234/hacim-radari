from typing import Any, Dict, List, Sequence
import math


def _num(x: Any, d=0.0):
    try:
        x = float(x)
        return x if math.isfinite(x) else d
    except Exception:
        return d


def _ema(v: Sequence[float], p: int):
    if not v:
        return []
    a = 2.0 / (p + 1)
    out = [_num(v[0])]
    for x in v[1:]:
        out.append(a * _num(x) + (1-a) * out[-1])
    return out


def _rsi(v, p=14):
    v = [_num(x) for x in v]
    if len(v) < p + 1:
        return [50.0] * len(v)

    gains = []
    losses = []

    for i in range(1, len(v)):
        d = v[i] - v[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    ag = sum(gains[:p]) / p
    al = sum(losses[:p]) / p
    out = [50.0] * len(v)

    def calc(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g/l)

    out[p] = calc(ag, al)

    for i in range(p+1, len(v)):
        ag = ((ag*(p-1)) + gains[i-1]) / p
        al = ((al*(p-1)) + losses[i-1]) / p
        out[i] = calc(ag, al)

    return out


def _macd_hist(v):
    e12 = _ema(v, 12)
    e26 = _ema(v, 26)
    m = [a-b for a,b in zip(e12, e26)]
    s = _ema(m, 9)
    return [a-b for a,b in zip(m, s)]


def _slope(v, w=3):
    if len(v) <= w:
        return 0.0
    a = _num(v[-w-1])
    b = _num(v[-1])
    return (b-a)/abs(a) if a else 0.0


def _change(a, b):
    return (b-a)/abs(a)*100 if a else 0.0


def analyze_kivrim(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
) -> Dict[str, Any]:

    n = min(
        len(highs),
        len(lows),
        len(closes),
        len(volumes)
    )

    if n < 60:
        return {
            "valid": False,
            "score": 0,
            "early_score": 0,
            "stage": "VERİ YETERSİZ",
            "reasons": [],
            "reasons_text": "",
        }

    highs = [_num(x) for x in highs[-n:]]
    lows = [_num(x) for x in lows[-n:]]
    closes = [_num(x) for x in closes[-n:]]
    volumes = [_num(x) for x in volumes[-n:]]

    price = closes[-1]

    ema7 = _ema(closes, 7)
    ema30 = _ema(closes, 30)
    r = _rsi(closes)
    mh = _macd_hist(closes)

    # EMA7 KIVRIM
    s0 = _slope(ema7, 3)
    s1 = _slope(ema7[:-1], 3)
    s2 = _slope(ema7[:-2], 3)

    curvature = s0 - s1
    curvature_prev = s1 - s2

    ema7_turning = s1 <= 0 < s0

    ema7_pre_turn = (
        not ema7_turning
        and s0 > s1
        and (
            abs(s0) <= 0.0035
            or curvature >= 0.00035
        )
    )

    ema7_accelerating = (
        curvature > 0
        and curvature >= curvature_prev
    )

    # EMA30
    e30_now = _slope(ema30, 4)
    e30_prev = _slope(ema30[:-1], 4)
    ema30_turning = e30_now > e30_prev

    # DIP
    recent_low = min(lows[-12:])
    previous_low = min(lows[-24:-12])

    near_recent_low = price <= recent_low * 1.035

    rejection = (
        lows[-1] <= min(lows[-5:])
        and closes[-1] > lows[-1]
        and closes[-1] >= closes[-2]
    )

    higher_low = (
        min(lows[-5:]) > previous_low * 1.0015
    )

    rising_lows = (
        lows[-1] >= lows[-2]
        and lows[-2] >= lows[-3]
    )

    # SATIŞ BASKISI
    down = sum(
        closes[i] < closes[i-1]
        for i in range(max(1, len(closes)-6), len(closes))
    )

    selling_fading = down <= 3

    exhaustion = (
        _change(closes[-6], closes[-1]) > -3.5
        and selling_fading
    )

    # RSI
    r0, r1, r2 = r[-1], r[-2], r[-3]

    rsi_turning = r0 > r1 >= r2
    rsi_early = 28 <= r0 <= 55
    rsi_recovery = 35 <= r0 <= 60 and r0 > r1

    # MACD
    h0, h1, h2 = mh[-1], mh[-2], mh[-3]

    macd_recovering = h0 > h1 >= h2

    macd_early = (
        h0 < 0
        and macd_recovering
    )

    # HACİM
    base_vol = sum(volumes[-21:-1]) / 20
    vr = volumes[-1] / base_vol if base_vol > 0 else 0

    volume_start = vr >= 1.10
    volume_strong = vr >= 1.35

    early_volume = (
        volume_start
        and abs(_change(closes[-2], closes[-1])) < 2.5
    )

    volume_rising = (
        volumes[-1] >= volumes[-2] >= volumes[-3]
    )

    # SIKIŞMA
    ranges = [
        (h-l)/max(c, 1e-12)
        for h,l,c in zip(
            highs[-20:],
            lows[-20:],
            closes[-20:]
        )
    ]

    compression = (
        sum(ranges[-5:])/5
        <= (sum(ranges)/len(ranges))*0.82
    )

    # EMA / FİYAT
    above_ema7 = price >= ema7[-1]

    reclaim_ema7 = (
        closes[-2] < ema7[-2]
        and closes[-1] >= ema7[-1]
    )

    near_ema7 = (
        abs(price-ema7[-1])/price*100 <= 1
    )

    # SON HAREKET
    move3 = _change(closes[-4], price)
    move2 = _change(closes[-3], price)

    late = 0

    if r0 >= 65:
        late += 8
    if r0 >= 70:
        late += 18
    if move3 >= 4:
        late += 8
    if move3 >= 7:
        late += 20
    if move2 >= 5:
        late += 12

    chase = late >= 20

    # SKOR
    score = 0
    reasons: List[str] = []

    def add(cond, pts, text):
        nonlocal score
        if cond:
            score += pts
            reasons.append(text)

    add(ema7_turning, 24, "EMA7 tam kıvrım")
    add(ema7_pre_turn, 22, "EMA7 PRE-KIVRIM")
    add(s0 > s1, 9, "EMA7 eğimi düzeliyor")
    add(ema7_accelerating, 8, "EMA7 ivmesi")
    add(ema30_turning, 5, "EMA30 dönüşü")

    add(near_recent_low, 9, "Dip bölgesi")
    add(rejection, 8, "Dipte satış reddi")
    add(higher_low, 9, "Higher-low")
    add(rising_lows, 5, "Dipler yükseliyor")
    add(exhaustion, 8, "Satış baskısı azalıyor")

    add(rsi_turning, 9, f"RSI dönüşü {r0:.1f}")
    add(rsi_early, 5, "RSI erken bölge")
    add(rsi_recovery, 4, "RSI toparlanıyor")

    add(macd_recovering, 9, "MACD toparlanıyor")
    add(macd_early, 5, "MACD erken dönüş")

    add(volume_start, 8, f"İlk hacim {vr:.2f}x")
    add(volume_strong, 4, "Hacim güçleniyor")
    add(early_volume, 6, "Dipte hacim")
    add(volume_rising, 3, "Hacim artıyor")

    add(compression, 6, "Sıkışma")
    add(above_ema7, 4, "EMA7 üzerinde")
    add(reclaim_ema7, 9, "EMA7 geri alımı")
    add(near_ema7, 3, "EMA7'ye yakın")

    # ERKEN YAPI BONUSU
    early_structure = (
        (ema7_pre_turn or ema7_turning)
        and (
            rsi_turning
            or macd_recovering
            or volume_start
        )
        and (
            near_recent_low
            or higher_low
            or exhaustion
        )
    )

    strong_early = (
        early_structure
        and not chase
    )

    if early_structure:
        score += 10
        reasons.append("ERKEN KIVRIM")

    if strong_early:
        score += 8
        reasons.append("1-3 MUM ERKEN ADAY")

    score = max(0, min(100, score))

    # AŞAMA
    confirmation = (
        ema7_turning
        and ema7_accelerating
        and (rsi_turning or macd_recovering)
        and volume_start
        and (above_ema7 or reclaim_ema7 or higher_low)
    )

    if chase:
        stage = "GEÇ"
    elif confirmation and score >= 70:
        stage = "TEYİT"
    elif strong_early and score >= 55:
        stage = "KIVRIM ÖNCÜ"
    elif early_structure and score >= 48:
        stage = "GÜÇLENEN KIVRIM"
    elif ema7_pre_turn and score >= 38:
        stage = "PRE-KIVRIM"
    else:
        stage = "BEKLE"

    early_score = max(
        0,
        min(100, score-late)
    )

    if strong_early:
        early_score = min(
            100,
            early_score + 5
        )

    if move3 >= 8 or r0 >= 75:
        early_score = min(
            early_score,
            30
        )
        stage = "GEÇ"

    return {
        "valid": True,
        "score": score,
        "early_score": early_score,
        "stage": stage,

        "price": price,

        "ema7": ema7[-1],
        "ema30": ema30[-1],

        "ema7_slope": s0,
        "ema7_slope_previous": s1,
        "ema7_curvature": curvature,

        "ema7_turning": ema7_turning,
        "ema7_pre_turn": ema7_pre_turn,
        "ema7_accelerating": ema7_accelerating,

        "ema30_slope": e30_now,
        "ema30_turning": ema30_turning,

        "near_recent_low": near_recent_low,
        "rejection_from_low": rejection,
        "higher_low": higher_low,
        "rising_lows": rising_lows,

        "selling_pressure_fading": selling_fading,
        "downside_exhaustion": exhaustion,

        "rsi": r0,
        "rsi_turning": rsi_turning,
        "rsi_early": rsi_early,

        "macd_hist": h0,
        "macd_recovering": macd_recovering,
        "macd_early": macd_early,

        "volume_ratio": vr,
        "volume_start": volume_start,
        "volume_strong": volume_strong,
        "early_volume": early_volume,

        "volume_rising": volume_rising,
        "compression": compression,

        "above_ema7": above_ema7,
        "reclaim_ema7": reclaim_ema7,
        "near_ema7": near_ema7,

        "move_3": move3,
        "move_2": move2,
        "late_penalty": late,
        "chase_risk": chase,

        "pre_curve_structure": early_structure,
        "strong_early_structure": strong_early,

        "reasons": reasons,
        "reasons_text": ", ".join(reasons),
    }


kivrim = analyze_kivrim
