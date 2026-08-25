from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OrderBookResult:
    bid_volume: float
    ask_volume: float
    imbalance: float
    bid_ratio: float
    bullish: bool
    score: int


class OrderBookImbalanceEngine:
    """
    Emir defteri dengesini hesaplar.

    imbalance:
        (bid - ask) / (bid + ask)

    Pozitif değer = alıcı tarafı güçlü.
    Negatif değer = satıcı tarafı güçlü.
    """

    def __init__(
        self,
        depth_levels: int = 10,
    ) -> None:
        self.depth_levels = depth_levels
        self.previous_ask_volume: float | None = None

    def update(
        self,
        bids: list[list[Any]],
        asks: list[list[Any]],
    ) -> OrderBookResult:
        bid_volume = self._sum_levels(bids)
        ask_volume = self._sum_levels(asks)

        total = bid_volume + ask_volume

        if total <= 0:
            imbalance = 0.0
            bid_ratio = 0.5
        else:
            imbalance = (
                bid_volume - ask_volume
            ) / total

            bid_ratio = (
                bid_volume / total
            )

        score = 0

        if imbalance >= 0.60:
            score = 100
        elif imbalance >= 0.40:
            score = 85
        elif imbalance >= 0.25:
            score = 70
        elif imbalance >= 0.10:
            score = 50
        elif imbalance > 0:
            score = 25

        if (
            self.previous_ask_volume is not None
            and ask_volume
            < self.previous_ask_volume * 0.75
            and imbalance > 0
        ):
            score = min(
                score + 15,
                100,
            )

        self.previous_ask_volume = ask_volume

        return OrderBookResult(
            bid_volume=bid_volume,
            ask_volume=ask_volume,
            imbalance=round(
                imbalance, 5
            ),
            bid_ratio=round(
                bid_ratio, 5
            ),
            bullish=imbalance > 0,
            score=score,
        )

    def _sum_levels(
        self,
        levels: list[list[Any]],
    ) -> float:
        total = 0.0

        for level in levels[
            : self.depth_levels
        ]:
            if len(level) < 2:
                continue

            try:
                total += float(level[1])
            except (TypeError, ValueError):
                continue

        return total

    @staticmethod
    def parse_depth(
        event: dict[str, Any],
    ) -> tuple[list[list[Any]], list[list[Any]]]:
        bids = event.get("bids", [])
        asks = event.get("asks", [])

        if not isinstance(bids, list):
            bids = []

        if not isinstance(asks, list):
            asks = []

        return bids, asks
        
