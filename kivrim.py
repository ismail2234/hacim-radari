"""
BALİNA RADARI V28 - KIVRIM MOTORU

Amaç:
    Dipten sonra başlayan ilk dönüş/kıvrım hareketini ölçmek.
    Bu modül tek başına AL/SELL vermez; scoring.py tarafından
    V28 erkenlik puanı olarak kullanılmak üzere tasarlanmıştır.

Ana fikir:
    dip -> satış zayıflaması -> EMA7 kıvrımı -> kıvrım ivmesi
    -> EMA30 dönüşü -> RSI dönüşü -> MACD toparlanması
    -> hacim başlangıcı -> higher-low

ÖNEMLİ:
    Bu motor geleceği kullanmaz. Sadece verilen mum ve geçmiş mumları
    kullanır. Böylece daha sonra mum-mum backtest yapılabilir.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence
import math


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except Exception:
        pass
    return default


def _ema(values: Sequence[float], period: int) -> List[float]:
    """Basit ve dış bağımlılıksız EMA."""
    values = [_num(x) for x in values]

    if not values:
        return []

    alpha = 2.0 / (period + 1.0)
    out = [values[0]]

    for value in values[1:]:
        out.append(
            alpha * value + (1.0 - alpha) * out[-1]
        )

    return out


def _rsi(values: Sequence[float], period: int = 14) -> List[float]:
    """Wilder RSI."""
    values = [_num(x) for x in values]

    if len(values) < period + 1:
        return [50.0] * len(values)

    result = [50.0] * len(values)

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def rsi_value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))

    result[period] = rsi_value(avg_gain, avg_loss)

    for i in range(period + 1, len(values)):
        avg_gain = (
            (avg_gain * (period - 1)) + gains[i - 1]
        ) / period
        avg_loss = (
            (avg_loss * (period - 1)) + losses[i - 1]
        ) / period

        result[i] = rsi_value(avg_gain, avg_loss)

    return result


def _macd_hist(values: Sequence[float]) -> List[float]:
    ema12 = _ema(values, 12)
    ema26 = _ema(values, 26)

    macd_line = [
        a - b for a, b in zip(ema12, ema26)
    ]

    signal = _ema(macd_line, 9)

    return [
        a - b for a, b in zip(macd_line, signal)
    ]


def _slope(series: Sequence[float], window: int = 3) -> float:
    """
    Son window değişiminin normalize edilmiş eğimi.
    Yüzdesel kullanılır; böylece 1 TL ve 100 TL coinler karşılaştırılabilir.
    """
    if len(series) <= window:
        return 0.0

    old = _num(series[-window - 1])
    new = _num(series[-1])

    if old == 0:
        return 0.0

    return (new - old) / abs(old)


def _slope_at(
    series: Sequence[float],
    end_index: int,
    window: int = 3,
) -> float:
    if end_index - window < 0:
        return 0.0

    old = _num(series[end_index - window])
    new = _num(series[end_index])

    if old == 0:
        return 0.0

    return (new - old) / abs(old)


def analyze_kivrim(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
) -> Dict[str, Any]:
    """
    Son mumu analiz eder.

    Minimum 60 mum önerilir.
    Dönen sözlük scoring.py tarafından doğrudan kullanılabilir.
    """

    n = min(
        len(highs),
        len(lows),
        len(closes),
        len(volumes),
    )

    if n < 60:
        return {
            "valid": False,
            "score": 0,
            "stage": "VERİ YETERSİZ",
            "reasons": [],
        }

    highs = [_num(x) for x in highs[-n:]]
    lows = [_num(x) for x in lows[-n:]]
    closes = [_num(x) for x in closes[-n:]]
    volumes = [_num(x) for x in volumes[-n:]]

    ema7 = _ema(closes, 7)
    ema30 = _ema(closes, 30)
    rsi = _rsi(closes, 14)
    macd_hist = _macd_hist(closes)

    # ---------------------------------------------------------
    # 1) EMA7 KIVRIMI
    # ---------------------------------------------------------
    ema7_slope_now = _slope(ema7, 3)
    ema7_slope_prev = _slope_at(
        ema7, len(ema7) - 2, 3
    )

    ema7_slope_prev2 = _slope_at(
        ema7, len(ema7) - 3, 3
    )

    # Eğimdeki değişim = kıvrım.
    ema7_curvature = (
        ema7_slope_now - ema7_slope_prev
    )

    ema7_curvature_prev = (
        ema7_slope_prev - ema7_slope_prev2
    )

    # Ana kıvrım: aşağı eğimden yataya/pozitife dönüş.
    ema7_turning = (
        ema7_slope_prev <= 0
        and ema7_slope_now > 0
    )

    # Henüz sıfırı geçmemiş ama kıvrım belirginleşiyor.
    ema7_pre_turn = (
        ema7_slope_now <= 0
        and ema7_curvature > 0
        and ema7_curvature >= abs(ema7_slope_prev) * 0.25
    )

    ema7_accelerating = (
        ema7_curvature > 0
        and ema7_curvature >= ema7_curvature_prev
    )

    # ---------------------------------------------------------
    # 2) EMA30 DÖNÜŞÜ
    # ---------------------------------------------------------
    ema30_slope_now = _slope(ema30, 4)
    ema30_slope_prev = _slope_at(
        ema30, len(ema30) - 2, 4
    )

    ema30_turning = (
        ema30_slope_now > ema30_slope_prev
    )

    # ---------------------------------------------------------
    # 3) DİP / SATIŞ ZAYIFLAMASI
    # ---------------------------------------------------------
    lookback = 12
    recent_low = min(lows[-lookback:])
    previous_low = min(lows[-lookback * 2:-lookback])

    price = closes[-1]

    near_recent_low = (
        price <= recent_low * 1.025
    )

    # Son mum yeni dip yapıp daha yukarı kapandıysa satış zayıflaması.
    rejection_from_low = (
        lows[-1] <= min(lows[-5:])
        and closes[-1] > lows[-1]
        and closes[-1] >= closes[-2]
    )

    # Son dip, önceki dipten daha yüksekse.
    higher_low = (
        min(lows[-5:]) > previous_low * 1.002
    )

    # Son 3 mumun dipleri yükseliyorsa dönüş yapısı güçleniyor.
    rising_lows = (
        lows[-1] >= lows[-2]
        and lows[-2] >= lows[-3]
    )

    # ---------------------------------------------------------
    # 4) RSI DİP KIVRIMI
    # ---------------------------------------------------------
    rsi_now = rsi[-1]
    rsi_prev = rsi[-2]
    rsi_prev2 = rsi[-3]

    rsi_turning = (
        rsi_now > rsi_prev
        and rsi_prev >= rsi_prev2
    )

    rsi_early = (
        25 <= rsi_now <= 55
    )

    # ---------------------------------------------------------
    # 5) MACD HISTOGRAM TOPARLANMASI
    # ---------------------------------------------------------
    hist_now = macd_hist[-1]
    hist_prev = macd_hist[-2]
    hist_prev2 = macd_hist[-3]

    macd_recovering = (
        hist_now > hist_prev
        and hist_prev >= hist_prev2
    )

    macd_early = (
        hist_now < 0
        and macd_recovering
    )

    # ---------------------------------------------------------
    # 6) HACİM BAŞLANGICI
    # ---------------------------------------------------------
    volume_base = sum(volumes[-21:-1]) / 20.0

    volume_ratio = (
        volumes[-1] / volume_base
        if volume_base > 0
        else 0.0
    )

    volume_start = (
        volume_ratio >= 1.15
    )

    volume_strong = (
        volume_ratio >= 1.40
    )

    # Fiyat yatay/dip bölgesindeyken hacim artışı daha değerlidir.
    early_volume = (
        volume_start
        and abs(
            closes[-1] - closes[-2]
        ) / max(closes[-2], 1e-12) < 0.025
    )

    # ---------------------------------------------------------
    # 7) SIKIŞMA
    # ---------------------------------------------------------
    ranges = [
        (h - l) / max(c, 1e-12)
        for h, l, c in zip(
            highs[-20:],
            lows[-20:],
            closes[-20:],
        )
    ]

    recent_range = sum(ranges[-5:]) / 5.0
    base_range = sum(ranges) / len(ranges)

    compression = (
        base_range > 0
        and recent_range <= base_range * 0.80
    )

    # ---------------------------------------------------------
    # 8) FİYAT / EMA YAPISI
    # ---------------------------------------------------------
    above_ema7 = price >= ema7[-1]

    reclaim_ema7 = (
        closes[-2] < ema7[-2]
        and closes[-1] >= ema7[-1]
    )

    # ---------------------------------------------------------
    # 9) SKOR
    # ---------------------------------------------------------
    score = 0
    reasons: List[str] = []

    # Ana motor: kıvrım en yüksek ağırlığa sahip.
    if ema7_turning:
        score += 25
        reasons.append("EMA7 tam kıvrım")
    elif ema7_pre_turn:
        score += 18
        reasons.append("EMA7 kıvrım hazırlığı")

    if ema7_accelerating:
        score += 8
        reasons.append("EMA7 ivmesi artıyor")

    if ema30_turning:
        score += 6
        reasons.append("EMA30 dönüşü")

    if near_recent_low:
        score += 8
        reasons.append("Dip bölgesi")

    if rejection_from_low:
        score += 7
        reasons.append("Dipte satış zayıflaması")

    if higher_low:
        score += 8
        reasons.append("Higher-low")

    if rising_lows:
        score += 5
        reasons.append("Dipler yükseliyor")

    if rsi_turning:
        score += 8
        reasons.append(f"RSI dönüşü ({rsi_now:.1f})")

    if rsi_early:
        score += 5
        reasons.append("RSI erken bölge")

    if macd_recovering:
        score += 8
        reasons.append("MACD histogram toparlanıyor")

    if macd_early:
        score += 4
        reasons.append("MACD negatif bölgede dönüş")

    if volume_start:
        score += 7
        reasons.append(f"Hacim başlangıcı ({volume_ratio:.2f}x)")

    if volume_strong:
        score += 4
        reasons.append("Hacim güçleniyor")

    if early_volume:
        score += 5
        reasons.append("Dipte hacim girişi")

    if compression:
        score += 5
        reasons.append("Sıkışma")

    if above_ema7:
        score += 3
        reasons.append("Fiyat EMA7 üzerinde")

    if reclaim_ema7:
        score += 6
        reasons.append("EMA7 geri alımı")

    score = max(0, min(100, score))

    # ---------------------------------------------------------
    # 10) AŞAMA
    # ---------------------------------------------------------
    core_curve = (
        ema7_turning
        or ema7_pre_turn
    )

    early_core = (
        core_curve
        and (
            rsi_turning
            or macd_recovering
            or volume_start
            or rejection_from_low
        )
    )

    confirmation = (
        (
            ema7_turning
            and ema7_accelerating
        )
        and (
            rsi_turning
            or macd_recovering
        )
        and volume_start
        and (
            above_ema7
            or reclaim_ema7
            or higher_low
        )
    )

    if confirmation and score >= 72:
        stage = "TEYİT"
    elif early_core and score >= 58:
        stage = "GÜÇLENEN KIVRIM"
    elif core_curve and score >= 45:
        stage = "KIVRIM ÖNCÜ"
    else:
        stage = "BEKLE"

    # ---------------------------------------------------------
    # 11) GEÇ KALMA KONTROLÜ
    # ---------------------------------------------------------
    move_3 = (
        (price - closes[-4])
        / max(closes[-4], 1e-12)
        * 100
    )

    # Son hareket zaten çok büyümüşse erkenlik puanını düşür.
    late_penalty = 0

    if rsi_now >= 65:
        late_penalty += 10

    if rsi_now >= 70:
        late_penalty += 20

    if move_3 >= 4:
        late_penalty += 8

    if move_3 >= 7:
        late_penalty += 20

    early_score = max(
        0,
        min(100, score - late_penalty),
    )

    # Aşırı hareket olmuşsa yeni öncü sinyal üretme.
    if late_penalty >= 30:
        stage = "GEÇ"

    return {
        "valid": True,
        "score": score,
        "early_score": early_score,
        "stage": stage,

        "price": price,

        "ema7": ema7[-1],
        "ema30": ema30[-1],

        "ema7_slope": ema7_slope_now,
        "ema7_slope_previous": ema7_slope_prev,
        "ema7_curvature": ema7_curvature,
        "ema7_turning": ema7_turning,
        "ema7_pre_turn": ema7_pre_turn,
        "ema7_accelerating": ema7_accelerating,

        "ema30_slope": ema30_slope_now,
        "ema30_turning": ema30_turning,

        "near_recent_low": near_recent_low,
        "rejection_from_low": rejection_from_low,
        "higher_low": higher_low,
        "rising_lows": rising_lows,

        "rsi": rsi_now,
        "rsi_turning": rsi_turning,
        "rsi_early": rsi_early,

        "macd_hist": hist_now,
        "macd_recovering": macd_recovering,
        "macd_early": macd_early,

        "volume_ratio": volume_ratio,
        "volume_start": volume_start,
        "volume_strong": volume_strong,
        "early_volume": early_volume,

        "compression": compression,
        "above_ema7": above_ema7,
        "reclaim_ema7": reclaim_ema7,

        "move_3": move_3,
        "late_penalty": late_penalty,

        "reasons": reasons,
        "reasons_text": ", ".join(reasons),
    }


# scoring.py içinden kullanılabilecek kısa isim.
kivrim = analyze_kivrim
