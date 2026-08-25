from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import time


@dataclass
class PriceVelocityResult:
    price_change_pct: float
    velocity_pct_per_second: float
    acceleration: float
    bullish: bool
    score: int


class PriceVelocityEngine:
    """
    Tick bazlı fiyat hızını ölçer.

    Amaç:
    Normal yükseliş ile çok kısa sürede başlayan
    agresif hareketi birbirinden ayırmak.
    """

    def __init__(
        self,
        window_seconds: int = 5,
    ) -> None:
        self.window_seconds = window_seconds
        self.prices: deque[tuple[float, float]] = deque()

        self.previous_velocity = 0.0

    def update(
        self,
        price: float,
        timestamp: float | None = None,
    ) -> PriceVelocityResult:
        now = (
            float(timestamp)
            if timestamp is not None
            else time()
        )

        price = float(price)

        if price <= 0:
            return PriceVelocityResult(
                0.0, 0.0, 0.0, False, 0
            )

        self.prices.append((now, price))

        oldest = now - self.window_seconds

        while (
            self.prices
            and self.prices[0][0] < oldest
        ):
            self.prices.popleft()

        if len(self.prices) < 2:
            return PriceVelocityResult(
                0.0, 0.0, 0.0, False, 0
            )

        old_time, old_price = self.prices[0]

        elapsed = max(now - old_time, 0.001)

        change_pct = (
            (price - old_price)
            / old_price
            * 100
        )

        velocity = change_pct / elapsed

        acceleration = (
            velocity
            - self.previous_velocity
        )

        self.previous_velocity = velocity

        score = self._score(
            velocity,
            acceleration,
        )

        return PriceVelocityResult(
            price_change_pct=round(
                change_pct, 5
            ),
            velocity_pct_per_second=round(
                velocity, 5
            ),
            acceleration=round(
                acceleration, 5
            ),
            bullish=velocity > 0,
            score=score,
        )

    @staticmethod
    def _score(
        velocity: float,
        acceleration: float,
    ) -> int:
        score = 0

        if velocity >= 0.02:
            score += 35
        elif velocity >= 0.01:
            score += 25
        elif velocity >= 0.005:
            score += 15

        if acceleration >= 0.02:
            score += 40
        elif acceleration >= 0.01:
            score += 25
        elif acceleration > 0:
            score += 10

        return min(score, 100)
        
