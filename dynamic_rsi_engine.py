from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class DynamicRSIResult:
    rsi: float
    slope: float
    acceleration: float
    bullish: bool
    score: int


class DynamicRSIEngine:
    """
    RSI seviyesinden çok RSI hareketini ölçer.

    Örneğin RSI 38'den 43'e hızlı çıkıyorsa,
    mutlak RSI değeri düşük olsa bile momentum
    oluştuğunu tespit eder.
    """

    def __init__(
        self,
        history_size: int = 8,
    ) -> None:
        self.values: deque[float] = deque(
            maxlen=history_size
        )
        self.previous_slope = 0.0

    def update(
        self,
        rsi: float,
    ) -> DynamicRSIResult:
        rsi = max(
            0.0,
            min(100.0, float(rsi)),
        )

        self.values.append(rsi)

        if len(self.values) < 2:
            return DynamicRSIResult(
                rsi=rsi,
                slope=0.0,
                acceleration=0.0,
                bullish=False,
                score=0,
            )

        slope = (
            self.values[-1]
            - self.values[-2]
        )

        acceleration = (
            slope
            - self.previous_slope
        )

        self.previous_slope = slope

        score = 0

        if slope >= 3:
            score += 50
        elif slope >= 1.5:
            score += 35
        elif slope > 0:
            score += 15

        if acceleration >= 2:
            score += 35
        elif acceleration > 0:
            score += 15

        # Çok yüksek RSI'da erkenlik puanını düşür.
        if rsi >= 75:
            score = int(score * 0.5)

        return DynamicRSIResult(
            rsi=round(rsi, 3),
            slope=round(slope, 3),
            acceleration=round(
                acceleration, 3
            ),
            bullish=slope > 0,
            score=min(score, 100),
        )
        
