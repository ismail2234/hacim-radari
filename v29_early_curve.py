from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class V29EarlyCurveResult:
    score: int
    label: str
    earlyness: int
    higher_low: bool
    ema7_reclaim: bool
    rsi_turn: bool
    selling_pressure_decreasing: bool
    reasons: list[str]


class V29EarlyCurve:
    """
    V29 erken kıvrım motoru.

    Amaç tepeyi yakalamak değil;
    dipten yükselişin davranış değişimini
    mümkün olduğunca erken yakalamaktır.
    """

    def calculate(
        self,
        *,
        close: float,
        ema7: float,
        ema7_prev: float,
        ema30: float,
        ema30_prev: float,
        rsi: float,
        rsi_prev: float,
        current_low: float,
        previous_low: float,
        volume_ratio: float,
        selling_pressure: float,
        previous_selling_pressure: float,
    ) -> V29EarlyCurveResult:

        score = 0
        reasons: list[str] = []

        # 1. EMA7 eğimi
        ema7_slope = ema7 - ema7_prev

        if ema7_slope > 0:
            score += 15
            reasons.append("EMA7 eğimi pozitif")

        elif ema7_slope >= 0:
            score += 8
            reasons.append("EMA7 eğimi düzeliyor")

        # 2. EMA30 dönüşü
        ema30_slope = ema30 - ema30_prev

        if ema30_slope > 0:
            score += 10
            reasons.append("EMA30 dönüşü")

        # 3. Fiyat EMA7'ye yaklaşımı
        if close > 0:
            distance_pct = (
                abs(close - ema7)
                / close
                * 100
            )

            if distance_pct <= 0.30:
                score += 15
                reasons.append("EMA7'ye çok yakın")

            elif distance_pct <= 0.75:
                score += 10
                reasons.append("EMA7'ye yakın")

        # 4. Higher-Low
        higher_low = (
            current_low > previous_low
        )

        if higher_low:
            score += 20
            reasons.append("Higher-Low")

        # 5. RSI dönüşü
        rsi_turn = rsi > rsi_prev

        if rsi_turn:
            score += 10
            reasons.append(
                f"RSI dönüşü {rsi:.1f}"
            )

        # 6. Erken RSI bölgesi
        if 35 <= rsi <= 55:
            score += 10
            reasons.append(
                "RSI erken bölge"
            )

        # 7. Satış baskısı azalıyor
        selling_pressure_decreasing = (
            selling_pressure
            < previous_selling_pressure
        )

        if selling_pressure_decreasing:
            score += 10
            reasons.append(
                "Satış baskısı azalıyor"
            )

        # 8. İlk hacim
        if volume_ratio >= 1.2:
            score += 5
            reasons.append(
                "İlk hacim"
            )

        score = max(
            0,
            min(100, score),
        )

        # Erkenlik: skor yüksek ama henüz
        # fazla yükselmemiş bölgeyi ödüllendir.
        earlyness = 0

        if 35 <= rsi <= 55:
            earlyness += 25

        if 1.1 <= volume_ratio <= 3.5:
            earlyness += 25

        if abs(close - ema7) / max(
            close, 0.00000001
        ) * 100 <= 1:
            earlyness += 25

        if not higher_low:
            earlyness += 10

        if rsi < 60:
            earlyness += 15

        earlyness = max(
            0,
            min(100, earlyness),
        )

        if (
            score >= 70
            and earlyness >= 70
        ):
            label = "KIVRIM ÖNCÜ"

        elif score >= 50:
            label = "PRE-KIVRIM"

        else:
            label = "BEKLE"

        return V29EarlyCurveResult(
            score=score,
            label=label,
            earlyness=earlyness,
            higher_low=higher_low,
            ema7_reclaim=close >= ema7,
            rsi_turn=rsi_turn,
            selling_pressure_decreasing=(
                selling_pressure_decreasing
            ),
            reasons=list(
                dict.fromkeys(reasons)
            ),
          )
      
