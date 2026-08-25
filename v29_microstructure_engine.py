from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dynamic_rsi_engine import DynamicRSIEngine
from order_book_engine import (
    OrderBookImbalanceEngine,
)
from price_velocity_engine import (
    PriceVelocityEngine,
)
from volume_spike_engine import (
    VolumeSpikeEngine,
)


@dataclass
class V29MicroResult:
    score: int
    volume_score: int
    velocity_score: int
    orderbook_score: int
    rsi_score: int
    volume_ratio: float
    price_velocity: float
    orderbook_imbalance: float
    rsi_slope: float
    reasons: list[str]


class V29MicrostructureEngine:
    """
    V29 gerçek zamanlı mikro yapı birleştiricisi.

    Bu sınıf henüz mevcut V29 kıvrım motoruna
    bağlanmaz. Önce bağımsız olarak güvenilir
    gerçek zamanlı skor üretir.
    """

    def __init__(self) -> None:
        self.volume = VolumeSpikeEngine()
        self.velocity = PriceVelocityEngine()
        self.orderbook = (
            OrderBookImbalanceEngine()
        )
        self.rsi = DynamicRSIEngine()

    def on_trade(
        self,
        price: float,
        quantity: float,
        timestamp: float | None = None,
    ) -> None:
        self.last_volume = self.volume.update(
            quantity,
            timestamp,
        )

        self.last_velocity = (
            self.velocity.update(
                price,
                timestamp,
            )
        )

    def on_depth(
        self,
        bids: list[list[Any]],
        asks: list[list[Any]],
    ) -> None:
        self.last_orderbook = (
            self.orderbook.update(
                bids,
                asks,
            )
        )

    def update_rsi(
        self,
        rsi: float,
    ) -> None:
        self.last_rsi = self.rsi.update(
            rsi
        )

    def score(self) -> V29MicroResult:
        volume = getattr(
            self,
            "last_volume",
            None,
        )

        velocity = getattr(
            self,
            "last_velocity",
            None,
        )

        orderbook = getattr(
            self,
            "last_orderbook",
            None,
        )

        rsi = getattr(
            self,
            "last_rsi",
            None,
        )

        volume_score = (
            volume.score if volume else 0
        )

        velocity_score = (
            velocity.score if velocity else 0
        )

        orderbook_score = (
            orderbook.score
            if orderbook
            else 0
        )

        rsi_score = (
            rsi.score if rsi else 0
        )

        score = round(
            volume_score * 0.30
            + velocity_score * 0.25
            + orderbook_score * 0.30
            + rsi_score * 0.15
        )

        reasons: list[str] = []

        if volume and volume.spike:
            reasons.append(
                f"Hacim spike {volume.volume_ratio:.2f}x"
            )

        if velocity and velocity.bullish:
            reasons.append(
                "Fiyat ivmesi pozitif"
            )

        if (
            orderbook
            and orderbook.bullish
        ):
            reasons.append(
                "Emir defteri alıcı baskılı"
            )

        if rsi and rsi.bullish:
            reasons.append(
                "RSI momentum yukarı"
            )

        return V29MicroResult(
            score=min(score, 100),
            volume_score=volume_score,
            velocity_score=velocity_score,
            orderbook_score=orderbook_score,
            rsi_score=rsi_score,
            volume_ratio=(
                volume.volume_ratio
                if volume
                else 0.0
            ),
            price_velocity=(
                velocity.velocity_pct_per_second
                if velocity
                else 0.0
            ),
            orderbook_imbalance=(
                orderbook.imbalance
                if orderbook
                else 0.0
            ),
            rsi_slope=(
                rsi.slope
                if rsi
                else 0.0
            ),
            reasons=reasons,
        )
        
