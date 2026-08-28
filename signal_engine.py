from __future__ import annotations

from typing import Any

import pandas as pd

from config import (
    MAX_LATE_MOVE_PERCENT,
    MAX_SINGLE_CANDLE_VOLUME_SPIKE,
    MIN_BUY_SCORE,
    MIN_VOLUME_TRY,
)


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        if pd.isna(value):
            return default

        return float(value)

    except Exception:
        return default


def _clamp(
    value: float,
    minimum: float,
    maximum: float,
) -> float:
    return max(
        minimum,
        min(maximum, value),
    )


class V30SignalEngine:
    """
    V30 erken yükseliş / kıvrım sinyal motoru.

    Amaç:
        Yükseliş başladıktan sonra kovalamak yerine,
        dipten gelen ilk talep değişimini yakalamak.

    Bu motor ilk aşamada sadece puan üretir.
    Backtest sonuçlarına göre eşikler geliştirilecektir.
    """

    def __init__(
        self,
        min_buy_score: float = MIN_BUY_SCORE,
    ) -> None:
        self.min_buy_score = min_buy_score

    # ========================================================
    # ANA FONKSİYON
    # ========================================================

    def analyze(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> dict[str, Any]:

        if df is None or df.empty:
            return self._empty_result(
                symbol,
                "NO_DATA",
            )

        if len(df) < 25:
            return self._empty_result(
                symbol,
                "INSUFFICIENT_DATA",
            )

        last = df.iloc[-1]

        price = _safe_float(
            last.get("close")
        )

        volume = _safe_float(
            last.get("quote_volume")
        )

        if price <= 0:
            return self._empty_result(
                symbol,
                "INVALID_PRICE",
            )

        if volume < MIN_VOLUME_TRY:
            return {
                "symbol": symbol,
                "signal": "IGNORE",
                "status": "LOW_VOLUME",
                "score": 0.0,
                "price": price,
                "volume_try": volume,
            }

        # ----------------------------------------------------
        # Bireysel puanlar
        # ----------------------------------------------------

        curve_score = self._curve_score(
            df
        )

        volume_score = self._volume_score(
            df
        )

        dip_score = self._dip_score(
            df
        )

        momentum_score = self._momentum_score(
            df
        )

        late_penalty = self._late_penalty(
            df
        )

        fakeout_penalty = self._fakeout_penalty(
            df
        )

        # ----------------------------------------------------
        # TOPLAM
        # ----------------------------------------------------

        positive_score = (
            curve_score
            + volume_score
            + dip_score
            + momentum_score
        )

        total_score = (
            positive_score
            - late_penalty
            - fakeout_penalty
        )

        total_score = _clamp(
            total_score,
            0.0,
            100.0,
        )

        signal = self._signal_from_score(
            total_score
        )

        status = self._status_from_score(
            total_score
        )

        return {
            "symbol": symbol,
            "signal": signal,
            "status": status,
            "score": round(
                total_score,
                2,
            ),
            "price": price,
            "volume_try": volume,
            "curve_score": round(
                curve_score,
                2,
            ),
            "volume_score": round(
                volume_score,
                2,
            ),
            "dip_score": round(
                dip_score,
                2,
            ),
            "momentum_score": round(
                momentum_score,
                2,
            ),
            "late_penalty": round(
                late_penalty,
                2,
            ),
            "fakeout_penalty": round(
                fakeout_penalty,
                2,
            ),
        }

    # ========================================================
    # KIVRIM PUANI
    # ========================================================

    def _curve_score(
        self,
        df: pd.DataFrame,
    ) -> float:

        if len(df) < 6:
            return 0.0

        closes = df["close"].tail(6)

        if closes.isna().any():
            return 0.0

        changes = (
            closes.pct_change()
            .dropna()
            * 100
        )

        if len(changes) < 5:
            return 0.0

        recent = changes.iloc[-3:]
        older = changes.iloc[:2]

        recent_avg = float(
            recent.mean()
        )

        older_avg = float(
            older.mean()
        )

        score = 0.0

        # Önceki baskı / yataylık
        if older_avg <= 0.30:
            score += 10

        # Son bölümde fiyat davranışı iyileşiyor mu?
        if recent_avg > older_avg:
            score += 8

        # Son mum pozitif
        if changes.iloc[-1] > 0:
            score += 4

        # Son üç mumun çoğu pozitif
        positive_count = int(
            (recent > 0).sum()
        )

        if positive_count >= 2:
            score += 3

        return _clamp(
            score,
            0,
            25,
        )

    # ========================================================
    # HACİM PUANI
    # ========================================================

    def _volume_score(
        self,
        df: pd.DataFrame,
    ) -> float:

        if len(df) < 20:
            return 0.0

        current = _safe_float(
            df.iloc[-1]["quote_volume"]
        )

        ma5 = _safe_float(
            df.iloc[-1].get(
                "volume_ma_5"
            )
        )

        ma20 = _safe_float(
            df.iloc[-1].get(
                "volume_ma_20"
            )
        )

        if current <= 0:
            return 0.0

        score = 0.0

        ratio5 = (
            current / ma5
            if ma5 > 0
            else 0
        )

        ratio20 = (
            current / ma20
            if ma20 > 0
            else 0
        )

        # Hacim ortalamanın üzerinde
        if ratio5 >= 1.15:
            score += 7

        if ratio5 >= 1.40:
            score += 5

        # 20 mumluk ortalamaya göre canlanma
        if ratio20 >= 1.15:
            score += 5

        if ratio20 >= 1.50:
            score += 3

        # Art arda hacim iyileşmesi
        volume_changes = (
            df["quote_volume"]
            .tail(4)
            .pct_change()
            .dropna()
        )

        if len(volume_changes) >= 2:
            rising = int(
                (volume_changes > 0)
                .sum()
            )

            if rising >= 2:
                score += 5

        return _clamp(
            score,
            0,
            25,
        )

    # ========================================================
    # DİP PUANI
    # ========================================================

    def _dip_score(
        self,
        df: pd.DataFrame,
    ) -> float:

        if len(df) < 20:
            return 0.0

        last = df.iloc[-1]

        position = _safe_float(
            last.get(
                "position_in_20_range"
            ),
            50.0,
        )

        score = 0.0

        # Alt bölgelerde daha fazla puan
        if position <= 35:
            score += 15

        elif position <= 50:
            score += 11

        elif position <= 65:
            score += 6

        # Aşırı tepede puan verme
        else:
            score += 0

        return _clamp(
            score,
            0,
            20,
        )

    # ========================================================
    # MOMENTUM
    # ========================================================

    def _momentum_score(
        self,
        df: pd.DataFrame,
    ) -> float:

        if len(df) < 6:
            return 0.0

        change3 = _safe_float(
            df.iloc[-1].get(
                "price_change_3"
            )
        )

        change5 = _safe_float(
            df.iloc[-1].get(
                "price_change_5"
            )
        )

        score = 0.0

        # Hafif pozitif momentum
        if change3 > 0:
            score += 5

        if change3 >= 0.5:
            score += 4

        if change5 > 0:
            score += 3

        # Çok hızlı kaçmamışsa
        if 0 <= change5 <= 4:
            score += 3

        return _clamp(
            score,
            0,
            15,
        )

    # ========================================================
    # GEÇ SİNYAL CEZASI
    # ========================================================

    def _late_penalty(
        self,
        df: pd.DataFrame,
    ) -> float:

        if len(df) < 6:
            return 0.0

        change5 = _safe_float(
            df.iloc[-1].get(
                "price_change_5"
            )
        )

        if change5 <= MAX_LATE_MOVE_PERCENT:
            return 0.0

        excess = (
            change5
            - MAX_LATE_MOVE_PERCENT
        )

        return _clamp(
            excess * 3.0,
            0,
            20,
        )

    # ========================================================
    # FAKEOUT CEZASI
    # ========================================================

    def _fakeout_penalty(
        self,
        df: pd.DataFrame,
    ) -> float:

        if len(df) < 5:
            return 0.0

        current_volume = _safe_float(
            df.iloc[-1]["quote_volume"]
        )

        previous_volume = _safe_float(
            df.iloc[-2]["quote_volume"]
        )

        if previous_volume <= 0:
            return 0.0

        volume_ratio = (
            current_volume
            / previous_volume
        )

        current_change = _safe_float(
            df.iloc[-1].get(
                "price_change_1"
            )
        )

        penalty = 0.0

        # Tek mumda aşırı hacim patlaması
        if (
            volume_ratio
            >= MAX_SINGLE_CANDLE_VOLUME_SPIKE
        ):
            penalty += 10

        # Hacim patlıyor ama fiyat karşılık vermiyor
        if (
            volume_ratio >= 2.5
            and current_change <= 0
        ):
            penalty += 8

        # Büyük negatif mum
        if current_change <= -2.0:
            penalty += 7

        return _clamp(
            penalty,
            0,
            20,
        )

    # ========================================================
    # SİNYAL
    # ========================================================

    def _signal_from_score(
        self,
        score: float,
    ) -> str:

        if score >= self.min_buy_score:
            return "BUY"

        if score >= 60:
            return "WATCH"

        return "IGNORE"

    # ========================================================
    # DURUM
    # ========================================================

    @staticmethod
    def _status_from_score(
        score: float,
    ) -> str:

        if score >= 75:
            return "EARLY_BUY"

        if score >= 60:
            return "STRENGTHENING"

        if score >= 40:
            return "WATCH"

        return "WEAK"

    # ========================================================
    # BOŞ SONUÇ
    # ========================================================

    @staticmethod
    def _empty_result(
        symbol: str,
        status: str,
    ) -> dict[str, Any]:

        return {
            "symbol": symbol,
            "signal": "IGNORE",
            "status": status,
            "score": 0.0,
            "price": 0.0,
            "volume_try": 0.0,
            "curve_score": 0.0,
            "volume_score": 0.0,
            "dip_score": 0.0,
            "momentum_score": 0.0,
            "late_penalty": 0.0,
            "fakeout_penalty": 0.0,
        }


signal_engine = V30SignalEngine()
