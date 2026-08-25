from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict

import math
import time

import pandas as pd

from indicators import add_indicators
from signal_engine import calculate_signal as calculate_v28_signal
from volume_engine import (
    calculate_volume_metrics,
    detect_volume_acceleration,
)


# ============================================================
# 🐋 BALİNA RADARI V29 ENGINE
# ============================================================
#
# V29 ANA HEDEF:
#
# Dipten başlayan hareketi mümkün olduğunca erken yakalamak.
#
# Özellikle:
#
#   • KIVRIM
#   • RSI toparlanması
#   • MACD dönüşü
#   • EMA yapısı
#   • Hacim artışı
#   • Hacim ivmesi
#   • Piyasa aktivitesi
#   • V28 teyidi
#
# birlikte değerlendirilir.
#
# V29 TEPEYİ YAKALAMAYA ÇALIŞMAZ.
#
# Amaç:
#
#       "Hareket başlamadan hemen önce
#        davranış değişti mi?"
#
# ============================================================


V29_VERSION = "V29"


# ============================================================
# TEMEL EŞİKLER
# ============================================================

MIN_SCORE = 60

EARLY_SCORE = 66

STRONG_SCORE = 76


# Hacim normal seviyenin biraz üzerinde olmalı.
MIN_VOLUME_RATIO = 1.05


# Hacim ivmesi.
MIN_VOLUME_ACCELERATION = 0.05


# RSI bölgeleri.
RSI_OVERSOLD = 30.0
RSI_RECOVERY = 42.0
RSI_MOMENTUM = 50.0
RSI_HIGH = 70.0


# MACD toleransı.
MACD_EPSILON = 0.0


# Aynı coin için tekrar sinyal süresi.
DEFAULT_SIGNAL_COOLDOWN = 20 * 60


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================


def _f(
    value: Any,
    default: float = 0.0,
) -> float:
    """
    Güvenli float dönüşümü.
    """

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
    """
    Güvenli integer dönüşümü.
    """

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
    """
    Değeri belirtilen aralıkta tutar.
    """

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
    """
    DataFrame son satırından güvenli değer alır.
    """

    if df is None or df.empty:
        return default

    if column not in df.columns:
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
    """
    DataFrame'deki bir önceki mum değerini alır.
    """

    if df is None or len(df) < 2:
        return default

    if column not in df.columns:
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
    """
    İki mum önceki değeri alır.

    KIVRIM tespitinde özellikle kullanılır.
    """

    if df is None or len(df) < 3:
        return default

    if column not in df.columns:
        return default

    return _f(
        df[column].iloc[-3],
        default,
    )


def _safe_pct(
    current: float,
    previous: float,
) -> float:
    """
    İki değer arasındaki yüzde değişimi döndürür.
    """

    if previous == 0:
        return 0.0

    return (
        (current - previous)
        / abs(previous)
    ) * 100.0


def _positive(
    value: float,
    threshold: float = 0.0,
) -> bool:

    return value > threshold


# ============================================================
# V29 SONUÇ NESNESİ
# ============================================================


@dataclass
class V29Result:

    symbol: str

    # Ana sonuç.
    score: int = 0
    signal: str = "BEKLE"

    # Teyit.
    confirmation: str = "YOK"

    # KIVRIM.
    curvature_score: int = 0
    curvature_type: str = "YOK"
    early_score: int = 0

    # Teknik skorlar.
    momentum_score: int = 0
    trend_score: int = 0
    volume_score: int = 0
    acceleration_score: int = 0

    # Fiyat.
    price: float = 0.0
    price_change: float = 0.0

    # RSI.
    rsi: float = 0.0

    # MACD.
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0

    # EMA.
    ema_fast: float = 0.0
    ema_slow: float = 0.0

    # Hacim.
    volume_ratio: float = 0.0
    volume_acceleration: float = 0.0

    # Kalite.
    quality: str = "ZAYIF"

    # Açıklamalar.
    reasons: list[str] | None = None

    # Filtre.
    rejected: bool = False
    reject_reason: str = ""

    # Zaman.
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
    """
    BALİNA RADARI V29 ana motoru.

    V28 tamamen kaldırılmaz.

    V29 yaklaşımı:

        V28 teyidi
        +
        KIVRIM
        +
        erken momentum
        +
        hacim
        +
        hacim ivmesi
        +
        piyasa aktivitesi

    """

    def __init__(
        self,
        min_score: int = MIN_SCORE,
        signal_cooldown: int = DEFAULT_SIGNAL_COOLDOWN,
    ) -> None:

        self.min_score = min_score

        self.signal_cooldown = signal_cooldown

        # Coin bazında son AL sinyal zamanı.
        self._last_signals: Dict[
            str,
            float,
        ] = {}

    # ========================================================
    # DATAFRAME HAZIRLAMA
    # ========================================================

    def prepare_dataframe(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Ham OHLCV verisini V29 analizine hazırlar.
        """

        if df is None or df.empty:
            return pd.DataFrame()

        work = df.copy()

        # ----------------------------------------------------
        # Kolon isimlerini normalize et.
        # ----------------------------------------------------

        work.columns = [
            str(column)
            .strip()
            .lower()
            for column in work.columns
        ]

        # ----------------------------------------------------
        # OHLCV sayısallaştır.
        # ----------------------------------------------------

        for column in (
            "open",
            "high",
            "low",
            "close",
            "volume",
        ):

            if column in work.columns:

                work[column] = pd.to_numeric(
                    work[column],
                    errors="coerce",
                )

        # ----------------------------------------------------
        # Temel fiyat verisi.
        # ----------------------------------------------------

        required = [
            column
            for column in (
                "open",
                "high",
                "low",
                "close",
            )
            if column in work.columns
        ]

        if required:

            work = work.dropna(
                subset=required
            )

        work = work.reset_index(
            drop=True
        )

        if work.empty:
            return work

        # ----------------------------------------------------
        # Mevcut indicators.py
        # ----------------------------------------------------

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

            # Eski sürüm inplace çalışıyor olabilir.
            try:
                add_indicators(work)
            except Exception:
                pass

        except Exception:
            pass

        # ----------------------------------------------------
        # EMA'lar
        # ----------------------------------------------------

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

            if "ema_50" not in work.columns:

                work["ema_50"] = (
                    work["close"]
                    .ewm(
                        span=50,
                        adjust=False,
                    )
                    .mean()
                )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if (
            "rsi" not in work.columns
            and "close" in work.columns
        ):

            delta = work["close"].diff()

            gain = delta.clip(
                lower=0
            )

            loss = -delta.clip(
                upper=0
            )

            avg_gain = gain.ewm(
                alpha=1 / 14,
                adjust=False,
            ).mean()

            avg_loss = loss.ewm(
                alpha=1 / 14,
                adjust=False,
            ).mean()

            avg_loss = avg_loss.replace(
                0,
                pd.NA,
            )

            rs = avg_gain / avg_loss

            work["rsi"] = (
                100
                - (
                    100
                    / (1 + rs)
                )
            )

            work["rsi"] = (
                work["rsi"]
                .fillna(50.0)
                .clip(0, 100)
            )

        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

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
                ema12 - ema26
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

        # ----------------------------------------------------
        # MACD histogram eksikse.
        # ----------------------------------------------------

        if (
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

        # ----------------------------------------------------
        # Hacim oranı.
        # ----------------------------------------------------

        if "volume" in work.columns:

            volume_ma = (
                work["volume"]
                .rolling(
                    20,
                    min_periods=1,
                )
                .mean()
            )

            work["v29_volume_ratio"] = (
                work["volume"]
                / volume_ma.replace(
                    0,
                    pd.NA,
                )
            )

            work[
                "v29_volume_ratio"
            ] = (
                work[
                    "v29_volume_ratio"
                ]
                .fillna(1.0)
            )

        # ----------------------------------------------------
        # Hacim ivmesi.
        # ----------------------------------------------------

        if "volume" in work.columns:

            previous_volume = (
                work["volume"]
                .shift(1)
            )

            work[
                "v29_volume_acceleration"
            ] = (
                work["volume"]
                / previous_volume.replace(
                    0,
                    pd.NA,
                )
            ) - 1.0

            work[
                "v29_volume_acceleration"
            ] = (
                work[
                    "v29_volume_acceleration"
                ]
                .replace(
                    [
                        float("inf"),
                        -float("inf"),
                    ],
                    0,
                )
                .fillna(0)
            )

        return work


    # ========================================================
    # TEKNİK DEĞERLERİ ÇIKAR
    # ========================================================

    def _extract_values(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, float]:

        close = _last(
            df,
            "close",
        )

        rsi = _last(
            df,
            "rsi",
            50.0,
        )

        macd = _last(
            df,
            "macd",
        )

        macd_signal = _last(
            df,
            "macd_signal",
        )

        histogram = _last(
            df,
            "macd_histogram",
            macd - macd_signal,
        )

        ema_fast = _last(
            df,
            "ema_9",
            close,
        )

        ema_slow = _last(
            df,
            "ema_20",
            close,
        )

        volume_ratio = _last(
            df,
            "v29_volume_ratio",
            1.0,
        )

        volume_acceleration = _last(
            df,
            "v29_volume_acceleration",
            0.0,
        )

        previous_close = _prev(
            df,
            "close",
            close,
        )

        price_change = _safe_pct(
            close,
            previous_close,
        )

        return {
            "price": close,
            "rsi": rsi,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_histogram": histogram,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "volume_ratio": volume_ratio,
            "volume_acceleration": volume_acceleration,
            "price_change": price_change,
        }
