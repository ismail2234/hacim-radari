from __future__ import annotations

from typing import Any

import pandas as pd

from indicators import add_indicators
from volume_engine import (
    calculate_volume_metrics,
    detect_volume_acceleration,
)


def _clamp_score(score: float) -> int:
    """Skoru 0-100 arasında sınırlar."""
    return max(0, min(100, int(round(score))))


def calculate_signal(
    df: pd.DataFrame,
) -> dict[str, Any]:
    """
    Hacim + teknik göstergeler üzerinden AL sinyali skoru üretir.

    Bu sürüm karar destek amaçlıdır.
    Otomatik emir göndermez.
    """

    if len(df) < 50:
        return {
            "signal": "WAIT",
            "score": 0,
            "reason": "En az 50 mum gerekli.",
        }

    data = add_indicators(df)

    volume = calculate_volume_metrics(data)
    volume_acceleration = detect_volume_acceleration(data)

    last = data.iloc[-1]

    score = 0
    reasons: list[str] = []

    # ---------------------------------------------------------
    # 1. HACİM
    # ---------------------------------------------------------

    volume_ratio = float(
        volume["volume_ratio"]
    )

    if volume_ratio >= 2.5:
        score += 30
        reasons.append("Çok güçlü hacim artışı")
    elif volume_ratio >= 2.0:
        score += 25
        reasons.append("Güçlü hacim artışı")
    elif volume_ratio >= 1.5:
        score += 18
        reasons.append("Hacim ortalamanın üzerinde")
    elif volume_ratio >= 1.2:
        score += 8

    # ---------------------------------------------------------
    # 2. HACİM İVMESİ
    # ---------------------------------------------------------

    if volume_acceleration:
        score += 15
        reasons.append("Hacim ivmeleniyor")

    # ---------------------------------------------------------
    # 3. RSI
    # ---------------------------------------------------------

    rsi = float(last["rsi_14"])

    if 45 <= rsi <= 65:
        score += 15
        reasons.append("RSI yükseliş için uygun bölgede")
    elif 35 <= rsi < 45:
        score += 8
        reasons.append("RSI düşük bölgeden toparlanıyor")
    elif 65 < rsi <= 72:
        score += 5
    elif rsi > 80:
        score -= 10
        reasons.append("RSI aşırı yüksek")

    # ---------------------------------------------------------
    # 4. MACD
    # ---------------------------------------------------------

    macd = float(last["macd"])
    macd_signal = float(last["macd_signal"])
    histogram = float(last["macd_histogram"])

    if macd > macd_signal:
        score += 10
        reasons.append("MACD pozitif")

    if histogram > 0:
        score += 5

    # ---------------------------------------------------------
    # 5. EMA TREND
    # ---------------------------------------------------------

    close = float(last["close"])
    ema9 = float(last["ema_9"])
    ema21 = float(last["ema_21"])
    ema50 = float(last["ema_50"])

    if close > ema9:
        score += 5

    if ema9 > ema21:
        score += 5
        reasons.append("Kısa vadeli trend pozitif")

    if ema21 > ema50:
        score += 5

    # ---------------------------------------------------------
    # 6. FİYAT HAREKETİ
    # ---------------------------------------------------------

    price_change = float(
        volume["price_change_pct"]
    )

    # Henüz aşırı yükselmemiş hareketleri tercih ediyoruz.
    if 0 < price_change <= 3:
        score += 10
        reasons.append("Fiyat henüz aşırı uzaklaşmadı")
    elif 3 < price_change <= 6:
        score += 3
    elif price_change > 8:
        score -= 10
        reasons.append("Fiyat fazla yükselmiş")

    # Negatif mumda güçlü AL puanı vermiyoruz.
    if not bool(volume["bullish_candle"]):
        score -= 5

    # ---------------------------------------------------------
    # SONUÇ
    # ---------------------------------------------------------

    score = _clamp_score(score)

    if score >= 75:
        signal = "BUY"
    elif score >= 60:
        signal = "WATCH"
    else:
        signal = "WAIT"

    return {
        "signal": signal,
        "score": score,
        "price": close,
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_histogram": histogram,
        "ema_9": ema9,
        "ema_21": ema21,
        "ema_50": ema50,
        "volume_ratio": volume_ratio,
        "volume_change_pct": float(
            volume["volume_change_pct"]
        ),
        "price_change_pct": price_change,
        "volume_acceleration": volume_acceleration,
        "reasons": reasons,
    }
