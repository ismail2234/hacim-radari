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
        idx: int = -1,
    ) -> dict[str, Any]:
        """
        Analyze dataframe at a specific index `idx` (defaults to -1 for current candle).
        Performance optimization: accepting `idx` allows backtesting over full DataFrames
        without slicing/copying DataFrames repeatedly.
        """
        if df is None or df.empty:
            return self._empty_result(
                symbol,
                "NO_DATA",
            )

        n = len(df)
        if idx < 0:
            idx = n + idx

        if idx < 0 or idx >= n:
            return self._empty_result(
                symbol,
                "NO_DATA",
            )

        if idx < 24:
            return self._empty_result(
                symbol,
                "INSUFFICIENT_DATA",
            )

        # Fast scalar retrieval at idx
        price = _safe_float(df["close"].iat[idx])
        volume = _safe_float(df["quote_volume"].iat[idx])

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
            df,
            idx,
        )

        volume_score = self._volume_score(
            df,
            idx,
        )

        dip_score = self._dip_score(
            df,
            idx,
        )

        momentum_score = self._momentum_score(
            df,
            idx,
        )

        late_penalty = self._late_penalty(
            df,
            idx,
        )

        fakeout_penalty = self._fakeout_penalty(
            df,
            idx,
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
        idx: int = -1,
    ) -> float:

        if idx < 5:
            return 0.0

        closes = df["close"].iloc[idx - 5 : idx + 1]

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
        idx: int = -1,
    ) -> float:

        if idx < 19:
            return 0.0

        current = _safe_float(
            df["quote_volume"].iat[idx]
        )

        ma5 = _safe_float(
            df["volume_ma_5"].iat[idx]
        )

        ma20 = _safe_float(
            df["volume_ma_20"].iat[idx]
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
            .iloc[max(0, idx - 3) : idx + 1]
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
        idx: int = -1,
    ) -> float:

        if idx < 19:
            return 0.0

        position = _safe_float(
            df["position_in_20_range"].iat[idx],
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
        idx: int = -1,
    ) -> float:

        if idx < 5:
            return 0.0

        change3 = _safe_float(
            df["price_change_3"].iat[idx]
        )

        change5 = _safe_float(
            df["price_change_5"].iat[idx]
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
        idx: int = -1,
    ) -> float:

        if idx < 5:
            return 0.0

        change5 = _safe_float(
            df["price_change_5"].iat[idx]
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
        idx: int = -1,
    ) -> float:

        if idx < 4:
            return 0.0

        current_volume = _safe_float(
            df["quote_volume"].iat[idx]
        )

        previous_volume = _safe_float(
            df["quote_volume"].iat[idx - 1]
        )

        if previous_volume <= 0:
            return 0.0

        volume_ratio = (
            current_volume
            / previous_volume
        )

        current_change = _safe_float(
            df["price_change_1"].iat[idx]
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
