"""
V26 - scoring.py  (4/8)
Butun motorlardan (hacim, momentum, yapi, MA, volatilite) gelen puanlari
birlestirip 0-100 arasi tek bir skor uretir.
"""

from config import CONFIG
from indicators import detect_consolidation
from signal_engines import score_volume, score_momentum, kdj_overbought


def score_price_structure(df) -> int:
    weight = CONFIG["score_weights"]["fiyat_yapisi"]
    return weight if detect_consolidation(df) else round(weight * 0.4)


def score_ma_alignment(df) -> int:
    weight = CONFIG["score_weights"]["ma_hizalanma"]
    last = df.iloc[-1]
    if last["close"] > last["ma7"] > last["ma30"]:
        return weight
    elif last["close"] > last["ma7"]:
        return round(weight * 0.53)
    return 0


def score_volatility(df) -> int:
    weight = CONFIG["score_weights"]["volatilite"]
    last = df.iloc[-1]
    atr_pct = last["atr"] / last["close"]
    return weight if 0.01 < atr_pct < 0.08 else round(weight * 0.4)


def score_candidate(df) -> dict:
    """Tum bilesenleri toplayip nihai skoru ve kirilimini dondurur."""
    breakdown = {
        "hacim": score_volume(df),
        "fiyat_yapisi": score_price_structure(df),
        "momentum": score_momentum(df),
        "ma_hizalanma": score_ma_alignment(df),
        "volatilite": score_volatility(df),
    }

    total = sum(breakdown.values())

    # KDJ asiri alimdaysa yeni pozisyon acmayi caydirmak icin ceza uygula
    if kdj_overbought(df):
        total = round(total * 0.7)
        breakdown["kdj_overbought_ceza"] = True

    return {"total": total, "breakdown": breakdown}


def classify_score(total: float) -> str:
    t = CONFIG["score_thresholds"]
    if total >= t["strong"]:
        return "GUCLU ADAY"
    elif total >= t["watch"]:
        return "IZLEME"
    elif total >= t["prep"]:
        return "HAZIRLIK"
    return "ISLEM YOK"
    
