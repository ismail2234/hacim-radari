from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfirmationResult:
    score: int
    confirmed: bool
    reasons: list[str]


class V29ConfirmationEngine:
    """
    Erken kıvrım sinyalini gerçek teyitlerden ayırır.

    Amaç:
    Erken sinyali öldürmeden, teyitsiz sinyali
    güçlü AL gibi göstermemektir.
    """

    def calculate(
        self,
        *,
        close: float,
        ema7: float,
        ema21: float,
        vwap: float,
        rsi: float,
        rsi_slope: float,
        macd_positive: bool,
        higher_low: bool,
        volume_ratio: float,
        orderbook_imbalance: float,
    ) -> ConfirmationResult:

        score = 0
        reasons: list[str] = []

        if close >= ema7:
            score += 15
            reasons.append("EMA7 geri alındı")

        if ema7 >= ema21:
            score += 15
            reasons.append("EMA7 > EMA21")

        if close >= vwap:
            score += 10
            reasons.append("VWAP geri alındı")

        if higher_low:
            score += 20
            reasons.append("Higher-Low teyidi")

        if rsi >= 45 and rsi_slope > 0:
            score += 10
            reasons.append("RSI momentum teyidi")

        if macd_positive:
            score += 10
            reasons.append("MACD pozitif")

        if volume_ratio >= 1.5:
            score += 10
            reasons.append("Hacim teyidi")

        if orderbook_imbalance >= 0.15:
            score += 10
            reasons.append("Emir defteri alıcı lehine")

        score = max(0, min(100, score))

        # En az birkaç bağımsız teyit olmadan
        # confirmed üretme.
        confirmed = (
            score >= 70
            and higher_low
            and close >= ema7
            and rsi_slope > 0
            and volume_ratio >= 1.5
        )

        return ConfirmationResult(
            score=score,
            confirmed=confirmed,
            reasons=reasons,
          )
      
