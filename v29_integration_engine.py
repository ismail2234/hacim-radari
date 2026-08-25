from __future__ import annotations

from typing import Any

import pandas as pd

from indicators import add_indicators
from v29_confirmation_engine import (
    V29ConfirmationEngine,
)
from v29_early_curve import V29EarlyCurve
from v29_fakeout_filter import (
    V29FakeoutFilter,
)
from v29_signal_fusion import V29SignalFusion


class V29IntegrationEngine:
    """
    V29 entegrasyon katmanı.

    Mevcut V28 motorunu değiştirmez.
    Yeni V29 parçalarını tek bir akışta birleştirir.

    Akış:
        Mum verisi
        -> indikatörler
        -> erken kıvrım
        -> teyit
        -> fakeout
        -> V29 skor
    """

    def __init__(self) -> None:
        self.curve = V29EarlyCurve()
        self.confirmation = V29ConfirmationEngine()
        self.fakeout = V29FakeoutFilter()
        self.fusion = V29SignalFusion()

    @staticmethod
    def _f(
        value: Any,
        default: float = 0.0,
    ) -> float:
        try:
            value = float(value)

            if pd.notna(value):
                return value

        except (
            TypeError,
            ValueError,
        ):
            pass

        return default

    @staticmethod
    def _bool(
        value: Any,
    ) -> bool:
        if isinstance(value, bool):
            return value

        return str(value).lower() in {
            "true",
            "1",
            "yes",
        }

    def calculate(
        self,
        df: pd.DataFrame,
        *,
        volume_ratio: float = 1.0,
        velocity: float = 0.0,
        orderbook_imbalance: float = 0.0,
        rsi_slope: float = 0.0,
        macd_positive: bool = False,
        micro_score: int = 0,
        fakeout_price_change_pct: float = 0.0,
        selling_pressure: float = 0.0,
        previous_selling_pressure: float = 0.0,
    ) -> dict[str, Any]:

        if len(df) < 35:
            return {
                "version": "V29",
                "signal": "WAIT",
                "status": "BEKLE",
                "score": 0,
                "reason": (
                    "V29 için en az 35 mum gerekli."
                ),
            }

        data = add_indicators(
            df.copy()
        )

        last = data.iloc[-1]
        prev = data.iloc[-2]

        close = self._f(
            last.get("close")
        )

        ema7 = self._f(
            last.get("ema_7")
        )

        ema7_prev = self._f(
            prev.get("ema_7")
        )

        ema30 = self._f(
            last.get("ema_30")
        )

        ema30_prev = self._f(
            prev.get("ema_30")
        )

        ema21 = self._f(
            last.get("ema_21")
        )

        rsi = self._f(
            last.get("rsi_14")
        )

        rsi_prev = self._f(
            prev.get("rsi_14")
        )

        current_low = self._f(
            last.get("low")
        )

        previous_low = self._f(
            prev.get("low")
        )

        # Erken kıvrım
        curve = self.curve.calculate(
            close=close,
            ema7=ema7,
            ema7_prev=ema7_prev,
            ema30=ema30,
            ema30_prev=ema30_prev,
            rsi=rsi,
            rsi_prev=rsi_prev,
            current_low=current_low,
            previous_low=previous_low,
            volume_ratio=volume_ratio,
            selling_pressure=selling_pressure,
            previous_selling_pressure=(
                previous_selling_pressure
            ),
        )

        # Teyit
        vwap = self._f(
            last.get("vwap"),
            close,
        )

        confirmation = (
            self.confirmation.calculate(
                close=close,
                ema7=ema7,
                ema21=ema21,
                vwap=vwap,
                rsi=rsi,
                rsi_slope=rsi_slope,
                macd_positive=macd_positive,
                higher_low=curve.higher_low,
                volume_ratio=volume_ratio,
                orderbook_imbalance=(
                    orderbook_imbalance
                ),
            )
        )

        # Fakeout
        fakeout = self.fakeout.calculate(
            volume_ratio=volume_ratio,
            price_change_pct=(
                fakeout_price_change_pct
            ),
            price_velocity=velocity,
            orderbook_imbalance=(
                orderbook_imbalance
            ),
            rsi_slope=rsi_slope,
            close_above_ema7=(
                close >= ema7
            ),
            higher_low=curve.higher_low,
        )

        # Mikro yapıdan gelen alt skorlar.
        volume_score = self._volume_score(
            volume_ratio
        )

        velocity_score = self._velocity_score(
            velocity
        )

        orderbook_score = self._orderbook_score(
            orderbook_imbalance
        )

        rsi_score = self._rsi_score(
            rsi_slope,
            rsi,
        )

        fusion = self.fusion.calculate(
            curve_score=curve.score,
            micro_score=micro_score,
            volume_score=volume_score,
            velocity_score=velocity_score,
            orderbook_score=orderbook_score,
            rsi_score=rsi_score,
            confirmation_score=(
                confirmation.score
            ),
            fakeout_penalty=(
                fakeout.penalty
            ),
            higher_low=curve.higher_low,
            ema7_reclaim=(
                close >= ema7
            ),
        )

        # V29 için önemli ayrım:
        # Erken aday ile teyitli AL aynı şey değildir.
        signal = fusion.signal
        status = fusion.status

        if (
            curve.earlyness >= 75
            and not confirmation.confirmed
        ):
            signal = "WATCH"
            status = "KIVRIM ÖNCÜ"

        if (
            confirmation.confirmed
            and fakeout.penalty < 35
            and fusion.score >= 78
        ):
            signal = "BUY"
            status = "KIVRIM TEYİTLİ"

        return {
            "version": "V29",
            "signal": signal,
            "status": status,
            "score": fusion.score,

            "early_score": (
                fusion.early_score
            ),

            "confirmation_score": (
                confirmation.score
            ),

            "confirmed": (
                confirmation.confirmed
            ),

            "curve_score": curve.score,

            "curve_label": curve.label,

            "curve_earlyness": (
                curve.earlyness
            ),

            "higher_low": (
                curve.higher_low
            ),

            "ema7_reclaim": (
                close >= ema7
            ),

            "fakeout_penalty": (
                fakeout.penalty
            ),

            "fakeout_risk": (
                fakeout.risk
            ),

            "volume_ratio": (
                volume_ratio
            ),

            "volume_score": (
                volume_score
            ),

            "velocity": velocity,

            "velocity_score": (
                velocity_score
            ),

            "orderbook_imbalance": (
                orderbook_imbalance
            ),

            "orderbook_score": (
                orderbook_score
            ),

            "rsi": rsi,

            "rsi_slope": rsi_slope,

            "rsi_score": rsi_score,

            "price": close,

            "ema7": ema7,

            "ema21": ema21,

            "ema30": ema30,

            "vwap": vwap,

            "curve_reasons": (
                curve.reasons
            ),

            "confirmation_reasons": (
                confirmation.reasons
            ),

            "fakeout_reasons": (
                fakeout.reasons
            ),

            "reasons": list(
                dict.fromkeys(
                    curve.reasons
                    + confirmation.reasons
                    + fakeout.reasons
                    + fusion.reasons
                )
            ),
        }

    @staticmethod
    def _volume_score(
        ratio: float,
    ) -> int:
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
    def _velocity_score(
        velocity: float,
    ) -> int:
        velocity = float(velocity)

        if velocity >= 0.05:
            return 100
        if velocity >= 0.03:
            return 85
        if velocity >= 0.02:
            return 70
        if velocity >= 0.01:
            return 50
        if velocity > 0:
            return 20

        return 0

    @staticmethod
    def _orderbook_score(
        imbalance: float,
    ) -> int:
        imbalance = float(imbalance)

        if imbalance >= 0.60:
            return 100
        if imbalance >= 0.40:
            return 85
        if imbalance >= 0.25:
            return 70
        if imbalance >= 0.10:
            return 50
        if imbalance > 0:
            return 25

        return 0

    @staticmethod
    def _rsi_score(
        slope: float,
        rsi: float,
    ) -> int:
        score = 0

        if slope >= 3:
            score += 60
        elif slope >= 1.5:
            score += 45
        elif slope > 0:
            score += 25

        if 35 <= rsi <= 55:
            score += 25

        return min(
            score,
            100,
        )
        
