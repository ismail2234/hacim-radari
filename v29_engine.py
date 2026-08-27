from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict
import math
import time

import pandas as pd

from indicators import add_indicators
from volume_engine import calculate_volume_metrics


# ============================================================
# 🐋 BALİNA RADARI V29
# ============================================================
#
# ANA HEDEF:
#
# Hacim yükselmeye başladığında,
# fiyat henüz fazla kaçmadan yakalamak.
#
# SİNYALLER:
#
#   AL
#   ÇOK GÜÇLÜ AL
#   BEKLE
#
# RSI / MACD / EMA:
#   Ana tetikleyici değildir.
#   Erken hareketi filtreler.
#
# ============================================================


V29_VERSION = "V29"


# ============================================================
# HACİM EŞİKLERİ
# ============================================================

MIN_VOLUME_RATIO = 1.05

AL_VOLUME_RATIO = 1.10

STRONG_VOLUME_RATIO = 2.00

SPIKE_VOLUME_RATIO = 3.00

MIN_VOLUME_ACCELERATION = 0.05


# ============================================================
# FİYAT EŞİKLERİ
# ============================================================

MAX_EARLY_PRICE_CHANGE = 3.5

MAX_CHASE_PRICE_CHANGE = 7.0


# ============================================================
# RSI
# ============================================================

RSI_HIGH = 70.0


# ============================================================
# COOLDOWN
# ============================================================

DEFAULT_SIGNAL_COOLDOWN = 20 * 60


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================


def _f(
    value: Any,
    default: float = 0.0,
) -> float:

    try:

        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", ".")

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except (TypeError, ValueError):

        return default


def _i(
    value: Any,
    default: int = 0,
) -> int:

    try:

        return int(
            round(
                _f(
                    value,
                    default,
                )
            )
        )

    except Exception:

        return default


def _clamp(
    value: float,
    low: float = 0.0,
    high: float = 100.0,
) -> float:

    return max(
        low,
        min(
            high,
            value,
        ),
    )


def _last(
    df: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> float:

    if (
        df is None
        or df.empty
        or column not in df.columns
    ):

        return default

    return _f(
        df[column].iloc[-1],
        default,
    )


def _prev(
    df: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> float:

    if (
        df is None
        or len(df) < 2
        or column not in df.columns
    ):

        return default

    return _f(
        df[column].iloc[-2],
        default,
    )


def _prev2(
    df: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> float:

    if (
        df is None
        or len(df) < 3
        or column not in df.columns
    ):

        return default

    return _f(
        df[column].iloc[-3],
        default,
    )


def _safe_pct(
    current: float,
    previous: float,
) -> float:

    if previous == 0:

        return 0.0

    return (
        (
            current
            - previous
        )
        / abs(previous)
    ) * 100.0


# ============================================================
# SONUÇ NESNESİ
# ============================================================


@dataclass
class V29Result:

    symbol: str

    score: int = 0

    signal: str = "BEKLE"

    confirmation: str = "YOK"

    price: float = 0.0

    price_change: float = 0.0

    rsi: float = 0.0

    macd: float = 0.0

    macd_signal: float = 0.0

    macd_histogram: float = 0.0

    ema_fast: float = 0.0

    ema_slow: float = 0.0

    volume_try: float = 0.0

    average_volume_try: float = 0.0

    volume_ratio: float = 0.0

    volume_change_pct: float = 0.0

    volume_acceleration: float = 0.0

    volume_building: bool = False

    volume_spike: bool = False

    quality: str = "ZAYIF"

    reasons: list[str] | None = None

    rejected: bool = False

    reject_reason: str = ""

    timestamp: float = 0.0

    def __post_init__(self) -> None:

        if self.reasons is None:

            self.reasons = []

        if not self.timestamp:

            self.timestamp = time.time()

    def to_dict(
        self,
    ) -> Dict[str, Any]:

        return asdict(self)


# ============================================================
# V29 ENGINE
# ============================================================


class V29Engine:

    def __init__(
        self,
        signal_cooldown: int = DEFAULT_SIGNAL_COOLDOWN,
    ) -> None:

        self.signal_cooldown = (
            signal_cooldown
        )

        self._last_signals: Dict[
            str,
            float,
        ] = {}

    # ========================================================
    # DATAFRAME HAZIRLA
    # ========================================================

    def prepare_dataframe(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        if (
            df is None
            or df.empty
        ):

            return pd.DataFrame()

        work = df.copy()

        # ----------------------------------------------------
        # Kolon isimleri
        # ----------------------------------------------------

        work.columns = [
            str(column)
            .strip()
            .lower()
            for column in work.columns
        ]

        # ----------------------------------------------------
        # Sayısal kolonlar
        # ----------------------------------------------------

        for column in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
        ):

            if column in work.columns:

                work[column] = pd.to_numeric(
                    work[column],
                    errors="coerce",
                )

        # ----------------------------------------------------
        # Fiyat verisi
        # ----------------------------------------------------

        required = [
            "open",
            "high",
            "low",
            "close",
        ]

        if all(
            column in work.columns
            for column in required
        ):

            work = work.dropna(
                subset=required
            )

        work = work.reset_index(
            drop=True
        )

        if work.empty:

            return work

        # ====================================================
        # MEVCUT İNDİKATÖRLER
        # ====================================================

        try:

            result = add_indicators(
                work
            )

            if isinstance(
                result,
                pd.DataFrame,
            ):

                work = result

        except TypeError:

            try:

                add_indicators(
                    work
                )

            except Exception:

                pass

        except Exception:

            pass

        # ====================================================
        # EMA
        # ====================================================

        if "close" in work.columns:

            if "ema_9" not in work.columns:

                work["ema_9"] = (
                    work["close"]
                    .ewm(
                        span=9,
                        adjust=False,
                    )
                    .mean()
                )

            if "ema_20" not in work.columns:

                work["ema_20"] = (
                    work["close"]
                    .ewm(
                        span=20,
                        adjust=False,
                    )
                    .mean()
                )

        # ====================================================
        # RSI
        # ====================================================

        if (
            "rsi" not in work.columns
            and "close" in work.columns
        ):

            delta = (
                work["close"]
                .diff()
            )

            gain = delta.clip(
                lower=0
            )

            loss = -delta.clip(
                upper=0
            )

            avg_gain = (
                gain
                .ewm(
                    alpha=1 / 14,
                    adjust=False,
                )
                .mean()
            )

            avg_loss = (
                loss
                .ewm(
                    alpha=1 / 14,
                    adjust=False,
                )
                .mean()
            )

            avg_loss = avg_loss.replace(
                0,
                pd.NA,
            )

            rs = (
                avg_gain
                / avg_loss
            )

            # ------------------------------------------------
            # RSI HESABI — TEK VE TEMİZ
            # ------------------------------------------------

            rsi_series = (
                100
                - (
                    100
                    / (1 + rs)
                )
            )

            # Pandas FutureWarning'i önlemek için
            # infer_objects önce uygulanıyor.
            rsi_series = (
                rsi_series
                .infer_objects(copy=False)
                .fillna(50.0)
                .clip(0, 100)
            )

            work["rsi"] = (
                rsi_series
            )

        # ====================================================
        # MACD
        # ====================================================

        if (
            "macd" not in work.columns
            and "close" in work.columns
        ):

            ema12 = (
                work["close"]
                .ewm(
                    span=12,
                    adjust=False,
                )
                .mean()
            )

            ema26 = (
                work["close"]
                .ewm(
                    span=26,
                    adjust=False,
                )
                .mean()
            )

            work["macd"] = (
                ema12
                - ema26
            )

            work["macd_signal"] = (
                work["macd"]
                .ewm(
                    span=9,
                    adjust=False,
                )
                .mean()
            )

            work["macd_histogram"] = (
                work["macd"]
                - work["macd_signal"]
            )

        elif (
            "macd_histogram"
            not in work.columns
            and "macd"
            in work.columns
            and "macd_signal"
            in work.columns
        ):

            work["macd_histogram"] = (
                work["macd"]
                - work["macd_signal"]
            )

        return work

    # ========================================================
    # DEĞERLERİ ÇIKAR
    # ========================================================

    def _extract_values(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, float]:

        close = _last(
            df,
            "close",
        )

        previous_close = _prev(
            df,
            "close",
            close,
        )

        return {

            "price": close,

            "price_change": _safe_pct(
                close,
                previous_close,
            ),

            "rsi": _last(
                df,
                "rsi",
                50.0,
            ),

            "macd": _last(
                df,
                "macd",
            ),

            "macd_signal": _last(
                df,
                "macd_signal",
            ),

            "macd_histogram": _last(
                df,
                "macd_histogram",
            ),

            "ema_fast": _last(
                df,
                "ema_9",
                close,
            ),

            "ema_slow": _last(
                df,
                "ema_20",
                close,
            ),
        }

    # ========================================================
    # HACİM ANALİZİ
    # ========================================================

    def _volume_analysis(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, Any]:

        try:

            metrics = (
                calculate_volume_metrics(
                    df
                )
            )

        except Exception as exc:

            return {
                "volume_try": 0.0,
                "average_volume_try": 0.0,
                "volume_ratio": 0.0,
                "volume_change_pct": 0.0,
                "volume_acceleration": 0.0,
                "volume_building": False,
                "volume_spike": False,
                "bullish_candle": False,
                "volume_trend_up": False,
                "early_volume_setup": False,
                "strong_volume_expansion": False,
                "_error": str(exc),
            }

        if not isinstance(
            metrics,
            dict,
        ):

            return {
                "volume_try": 0.0,
                "average_volume_try": 0.0,
                "volume_ratio": 0.0,
                "volume_change_pct": 0.0,
                "volume_acceleration": 0.0,
                "volume_building": False,
                "volume_spike": False,
                "bullish_candle": False,
                "volume_trend_up": False,
                "early_volume_setup": False,
                "strong_volume_expansion": False,
                "_error": "volume_engine dict döndürmedi",
            }

        return metrics
