from __future__ import annotations

from typing import Any

import pandas as pd

from indicators import add_indicators
from volume_engine import (
    calculate_volume_metrics,
    detect_volume_acceleration,
)


def _clamp_score(score: float) -> int:
    return max(0, min(100, int(round(score))))


def calculate_signal(df: pd.DataFrame) -> dict[str, Any]:
    """
    BALİNA RADARI V28 KIVRIM MOTORU.

    Amaç:
    Yükseliş başladıktan sonra değil,
    dipten dönüşün ilk kıvrım bölgesini yakalamaktır.
    """

    if len(df) < 60:
        return {
            "signal": "WAIT",
            "score": 0,
            "reason": "En az 60 mum gerekli.",
        }

    data = add_indicators(df)

    volume = calculate_volume_metrics(data)
    volume_acceleration = detect_volume_acceleration(data)

    last = data.iloc[-1]
    prev = data.iloc[-2]

    score = 0
    reasons: list[str] = []

    # ---------------------------------------------------------
    # 1 — KIVRIM
    # ---------------------------------------------------------

    curve_up = bool(last["curve_up"])

    if curve_up:
        score += 15
        reasons.append("Kıvrım aktif")

    # ---------------------------------------------------------
    # 2 — DİP
    # ---------------------------------------------------------

    near_dip = bool(last["near_dip"])

    if near_dip:
        score += 12
        reasons.append("Dip bölgesi")

    # ---------------------------------------------------------
    # 3 — HIGHER-LOW
    # ---------------------------------------------------------

    higher_low = bool(last["higher_low"])

    if higher_low:
        score += 12
        reasons.append("Higher-Low oluşuyor")

    # ---------------------------------------------------------
    # 4 — EMA7
    # ---------------------------------------------------------

    close = float(last["close"])
    ema7 = float(last["ema_7"])
    ema9 = float(last["ema_9"])
    ema21 = float(last["ema_21"])

    if close > ema7:
        score += 8
        reasons.append("Fiyat EMA7 üzerinde")

    if ema7 > ema9:
        score += 5

    if ema7 > ema21:
        score += 5
        reasons.append("EMA7 trend dönüşü")

    # ---------------------------------------------------------
    # 5 — RSI
    # ---------------------------------------------------------

    rsi = float(last["rsi_14"])
    previous_rsi = float(prev["rsi_14"])

    if 40 <= rsi <= 60:
        score += 8
        reasons.append("RSI dönüş bölgesinde")

    if rsi > previous_rsi:
        score += 4

    if previous_rsi < 40 <= rsi:
        score += 6
        reasons.append("RSI dipten dönüyor")

    # ---------------------------------------------------------
    # 6 — MACD
    # ---------------------------------------------------------

    macd = float(last["macd"])
    macd_signal = float(last["macd_signal"])
    histogram = float(last["macd_histogram"])
    previous_histogram = float(prev["macd_histogram"])

    if macd > macd_signal:
        score += 7
        reasons.append("MACD pozitif")

    if histogram > previous_histogram:
        score += 5
        reasons.append("MACD histogram güçleniyor")

    # ---------------------------------------------------------
    # 7 — HACİM
    # ---------------------------------------------------------

    volume_ratio = float(volume["volume_ratio"])

    if volume_ratio >= 2.5:
        score += 12
        reasons.append("TL hacmi çok güçlü")
    elif volume_ratio >= 2.0:
        score += 10
        reasons.append("TL hacmi güçlü")
    elif volume_ratio >= 1.5:
        score += 7
        reasons.append("TL hacmi artıyor")

    if volume_acceleration:
        score += 8
        reasons.append("Hacim ivmesi aktif")

    # ---------------------------------------------------------
    # 8 — ICHIMOKU
    # ---------------------------------------------------------

    conversion = float(last["ichimoku_conversion"])
    base = float(last["ichimoku_base"])
    span_a = float(last["ichimoku_span_a"])
    span_b = float(last["ichimoku_span_b"])

    if conversion > base:
        score += 4

    cloud_top = max(span_a, span_b)

    if close >= cloud_top:
        score += 5
        reasons.append("Ichimoku bulutu pozitif")

    # ---------------------------------------------------------
    # 9 — FIBONACCI
    # ---------------------------------------------------------

    fib_zone = bool(last["fib_zone"])

    if fib_zone:
        score += 5
        reasons.append("Fibonacci dönüş bölgesi")

    # ---------------------------------------------------------
    # 10 — VOLUME PROFILE
    # ---------------------------------------------------------

    volume_profile_support = bool(
        last["volume_profile_support"]
    )

    if volume_profile_support:
        score += 5
        reasons.append("Volume Profile destek bölgesi")

    # ---------------------------------------------------------
    # 11 — FİYAT ÇOK UZAKLAŞMASIN
    # ---------------------------------------------------------

    price_change = float(
        volume["price_change_pct"]
    )

    if price_change > 8:
        score -= 15
        reasons.append("Fiyat fazla yükselmiş")

    elif 0 <= price_change <= 3:
        score += 5
        reasons.append("Hareket henüz erken")

    # ---------------------------------------------------------
    # SONUÇ
    # ---------------------------------------------------------

    score = _clamp_score(score)

    # V28:
    # 75+ güçlü kıvrım
    # 65-74 izleme
    # altı bekle

    if score >= 75:
        signal = "BUY"

    elif score >= 65:
        signal = "WATCH"

    else:
        signal = "WAIT"

    return {
        "signal": signal,
        "score": score,
        "price": close,

        "curve_up": curve_up,
        "near_dip": near_dip,
        "higher_low": higher_low,

        "ema_7": ema7,
        "ema_9": ema9,
        "ema_21": ema21,

        "rsi": rsi,

        "macd": macd,
        "macd_signal": macd_signal,
        "macd_histogram": histogram,

        "volume_try": float(
            volume["volume_try"]
        ),
        "volume_ratio": volume_ratio,
        "volume_change_pct": float(
            volume["volume_change_pct"]
        ),
        "volume_acceleration": volume_acceleration,

        "ichimoku_conversion": conversion,
        "ichimoku_base": base,
        "ichimoku_span_a": span_a,
        "ichimoku_span_b": span_b,

        "fib_zone": fib_zone,
        "volume_profile_support": (
            volume_profile_support
        ),

        "price_change_pct": price_change,

        "reasons": reasons,
    }
