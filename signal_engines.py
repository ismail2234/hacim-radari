"""
V26 - signal_engines.py  (3/8)
Hacim analizi, momentum analizi, kirilim tespiti ve retest kontrolu
bu dosyada birlesik.
"""

from config import CONFIG
from indicators import detect_consolidation


# ============================================================
# HACIM ANALIZI
# ============================================================

def volume_spike_level(df) -> float:
    """Guncel hacmin ortalamaya oranini dondurur (2x, 3x, 5x gibi)."""
    last = df.iloc[-1]
    return float(last["vol_ratio"]) if not (last["vol_ratio"] != last["vol_ratio"]) else 0.0


def is_volume_anomaly(df, min_ratio: float = None) -> bool:
    min_ratio = min_ratio or CONFIG["vol_spike_ratio"]
    return volume_spike_level(df) >= min_ratio


def volume_quality(df, window=20) -> float:
    """
    Son 'window' mumda yukselis hacmi / toplam hacim orani.
    1.0'a yakinsa hacim cogunlukla alis yonunde, 0'a yakinsa satis yonunde.
    """
    recent = df.iloc[-window:]
    total = recent["volume"].sum()
    if total == 0:
        return 0.5
    return float(recent["up_volume"].sum() / total)


def score_volume(df) -> int:
    """Hacim bileseni icin 0-25 puan (config agirligina gore)."""
    weight = CONFIG["score_weights"]["hacim"]
    ratio = volume_spike_level(df)
    quality = volume_quality(df)

    if ratio >= 3:
        base = weight
    elif ratio >= 2:
        base = weight * 0.72
    elif ratio >= 1.5:
        base = weight * 0.4
    else:
        base = 0

    if quality < 0.4:
        base *= 0.6

    return round(base)


# ============================================================
# MOMENTUM ANALIZI
# ============================================================

def rsi_momentum_ok(df, low=40, high=55) -> bool:
    """RSI bandin icinde VE yukari yonlu mu (sadece esik degil, hiz da onemli)."""
    last = df.iloc[-1]
    return bool(low <= last["rsi"] <= high and last["rsi_slope"] > 0)


def macd_turning_positive(df) -> bool:
    """MACD histogrami negatiften pozitife donuyor mu."""
    if len(df) < 2:
        return False
    return bool(df["macd_hist"].iloc[-1] > 0 and df["macd_hist"].iloc[-2] <= 0)


def kdj_bullish_cross(df, upper_limit=80) -> bool:
    """K, D'yi yukari kesmis mi VE asiri alim bolgesinde degil mi."""
    last = df.iloc[-1]
    return bool(last["kdj_k"] > last["kdj_d"] and last["kdj_k"] < upper_limit)


def kdj_overbought(df, limit=70) -> bool:
    """KDJ asiri alim bolgesinde mi -- yeni pozisyon acmamak icin filtre."""
    return bool(df.iloc[-1]["kdj_k"] >= limit)


def score_momentum(df) -> int:
    """Momentum bileseni icin 0-30 puan (config agirligina gore)."""
    weight = CONFIG["score_weights"]["momentum"]
    score = 0

    if rsi_momentum_ok(df):
        score += weight * 0.4
    if macd_turning_positive(df):
        score += weight * 0.33
    if kdj_bullish_cross(df):
        score += weight * 0.27

    return round(score)


# ============================================================
# KIRILIM TESPITI (false breakout filtresi dahil)
# ============================================================

def find_recent_resistance(df, window=30) -> float:
    """Son 'window' mumun en yuksek kapanisini direnc olarak alir (basit yontem)."""
    return float(df["high"].iloc[-window:].max())


def is_breakout_setup(df) -> bool:
    """Sikisma + fiyat direnc yakininda ise 'kirilim hazirligi' sinyali."""
    if not detect_consolidation(df):
        return False
    resistance = find_recent_resistance(df)
    last_close = df["close"].iloc[-1]
    return bool(last_close >= resistance * 0.97)


def confirm_breakout(df, resistance_level: float = None, min_candles_above: int = 2,
                      min_volume_ratio: float = 1.2) -> bool:
    """
    False breakout filtresi:
    Tek mum direnc ustune ciktiginda hemen AL demez;
    ardisik mumlarin direnc ustunde kapanmasi VE hacim destegi arar.
    """
    if resistance_level is None:
        resistance_level = find_recent_resistance(df, window=30)

    if len(df) < min_candles_above:
        return False

    last_n = df.iloc[-min_candles_above:]
    closed_above = bool((last_n["close"] > resistance_level).all())
    volume_ok = bool((last_n["vol_ratio"] > min_volume_ratio).any())

    return closed_above and volume_ok


# ============================================================
# RETEST KONTROLU
# ============================================================

def is_retest_holding(df, breakout_level: float, tolerance_pct: float = 0.02,
                       lookback: int = 5) -> bool:
    """
    Kirilimdan sonraki son 'lookback' mumda fiyat, kirilan seviyenin
    (artik destek olan) altina anlamli sekilde sarkip sarkmadigini kontrol eder.
    """
    if len(df) < lookback:
        return False

    recent = df.iloc[-lookback:]
    min_low = recent["low"].min()
    threshold = breakout_level * (1 - tolerance_pct)

    held = min_low >= threshold
    closed_above_again = recent["close"].iloc[-1] > breakout_level

    return bool(held and closed_above_again)


def retest_status(df, breakout_level: float) -> str:
    """Insan-okunur durum metni dondurur: 'bekleniyor' / 'tutuyor' / 'kirildi'."""
    last_close = df["close"].iloc[-1]
    if last_close > breakout_level * 1.03:
        return "retest_henuz_gelmedi"
    if is_retest_holding(df, breakout_level):
        return "retest_tutuyor"
    return "retest_kirildi_veya_belirsiz"
