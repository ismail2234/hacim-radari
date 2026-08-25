from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import time
from typing import Any


@dataclass
class VolumeSpikeResult:
    volume_ratio: float
    current_volume: float
    average_volume: float
    spike: bool
    level: str


class VolumeSpikeEngine:
    """
    Gerçek zamanlı hacim anormalliği motoru.

    Son birkaç saniyedeki işlem hacmini,
    geçmiş kısa periyot hacmiyle karşılaştırır.
    """

    def __init__(
        self,
        window_seconds: int = 10,
        baseline_seconds: int = 60,
        spike_threshold: float = 3.0,
        strong_threshold: float = 5.0,
        extreme_threshold: float = 10.0,
    ) -> None:
        self.window_seconds = window_seconds
        self.baseline_seconds = baseline_seconds
        self.spike_threshold = spike_threshold
        self.strong_threshold = strong_threshold
        self.extreme_threshold = extreme_threshold

        self.trades: deque[tuple[float, float]] = deque()

    def update(
        self,
        quantity: float,
        timestamp: float | None = None,
    ) -> VolumeSpikeResult:
        now = (
            float(timestamp)
            if timestamp is not None
            else time()
        )

        quantity = max(float(quantity), 0.0)

        self.trades.append((now, quantity))
        self._cleanup(now)

        current_start = now - self.window_seconds
        baseline_start = now - self.baseline_seconds

        current_volume = sum(
            qty
            for ts, qty in self.trades
            if ts >= current_start
        )

        baseline_volume = sum(
            qty
            for ts, qty in self.trades
            if ts >= baseline_start
            and ts < current_start
        )

        baseline_periods = max(
            self.baseline_seconds / self.window_seconds - 1,
            1.0,
        )

        average_volume = (
            baseline_volume / baseline_periods
        )

        if average_volume <= 0:
            ratio = 0.0
        else:
            ratio = current_volume / average_volume

        if ratio >= self.extreme_threshold:
            level = "EXTREME"
        elif ratio >= self.strong_threshold:
            level = "STRONG"
        elif ratio >= self.spike_threshold:
            level = "SPIKE"
        else:
            level = "NORMAL"

        return VolumeSpikeResult(
            volume_ratio=round(ratio, 4),
            current_volume=current_volume,
            average_volume=average_volume,
            spike=ratio >= self.spike_threshold,
            level=level,
        )

    def _cleanup(self, now: float) -> None:
        oldest = now - self.baseline_seconds

        while (
            self.trades
            and self.trades[0][0] < oldest
        ):
            self.trades.popleft()

    def calculate_score(
        self,
        result: VolumeSpikeResult,
    ) -> int:
        ratio = result.volume_ratio

        if ratio >= 10:
            return 100
        if ratio >= 7:
            return 90
        if ratio >= 5:
            return 80
        if ratio >= 3:
            return 65
        if ratio >= 2:
            return 45
        if ratio >= 1.5:
            return 25

        return 0

    @staticmethod
    def trade_quantity(event: dict[str, Any]) -> float:
        """
        Binance trade/aggTrade mesajından miktarı okur.
        """

        for key in ("q", "quantity", "Q"):
            value = event.get(key)

            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0

        return 0.0
        
