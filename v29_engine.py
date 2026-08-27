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
            rsi_series = pd.to_numeric(
    rsi_series,
    errors="coerce",
)

rsi_series = rsi_series.where(
    rsi_series.notna(),
    50.0,
)

rsi_series = rsi_series.clip(
    0,
    100,
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
    def _volume_score(
        self,
        volume: Dict[str, Any],
    ) -> tuple[int, list[str]]:

        score = 0
        reasons: list[str] = []

        ratio = _f(
            volume.get("volume_ratio"),
            0.0,
        )

        acceleration = _f(
            volume.get("volume_acceleration"),
            0.0,
        )

        building = bool(
            volume.get("volume_building")
        )

        spike = bool(
            volume.get("volume_spike")
        )

        trend_up = bool(
            volume.get("volume_trend_up")
        )

        early_setup = bool(
            volume.get("early_volume_setup")
        )

        if ratio >= MIN_VOLUME_RATIO:
            score += 15
            reasons.append(
                "hacim normalin üzerine çıktı"
            )

        if ratio >= AL_VOLUME_RATIO:
            score += 10
            reasons.append(
                "hacim yükseliyor"
            )

        if ratio >= 1.50:
            score += 15
            reasons.append(
                "hacim belirgin güçleniyor"
            )

        if ratio >= STRONG_VOLUME_RATIO:
            score += 20
            reasons.append(
                "hacim güçlü yükseliyor"
            )

        if ratio >= SPIKE_VOLUME_RATIO:
            score += 20
            reasons.append(
                "hacim patlaması"
            )

        if acceleration >= 0.05:
            score += 10
            reasons.append(
                "hacim ivmesi pozitif"
            )

        if acceleration >= 0.15:
            score += 10
            reasons.append(
                "hacim ivmesi çok güçlü"
            )

        if building:
            score += 10
            reasons.append(
                "hacim kademeli yükseliyor"
            )

        if trend_up:
            score += 5
            reasons.append(
                "hacim trendi yukarı"
            )

        if early_setup:
            score += 10
            reasons.append(
                "erken hacim oluşumu"
            )

        if spike:
            score += 10
            reasons.append(
                "hacim spike teyidi"
            )

        return (
            min(score, 100),
            reasons,
        )


    def _momentum_filter(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0
        reasons: list[str] = []

        price_change = values[
            "price_change"
        ]

        rsi = values[
            "rsi"
        ]

        macd = values[
            "macd"
        ]

        macd_signal = values[
            "macd_signal"
        ]

        histogram = values[
            "macd_histogram"
        ]

        previous_rsi = _prev(
            df,
            "rsi",
            rsi,
        )

        previous_histogram = _prev(
            df,
            "macd_histogram",
            histogram,
        )

        ema_fast = values[
            "ema_fast"
        ]

        ema_slow = values[
            "ema_slow"
        ]

        # ----------------------------------------------------
        # FİYAT
        # ----------------------------------------------------

        if (
            0.0
            <= price_change
            <= MAX_EARLY_PRICE_CHANGE
        ):

            score += 20

            reasons.append(
                "fiyat henüz erken bölgede"
            )

        elif (
            -2.0
            <= price_change
            < 0.0
        ):

            score += 10

            reasons.append(
                "fiyat dipten toparlanıyor"
            )

        elif (
            price_change
            > MAX_CHASE_PRICE_CHANGE
        ):

            reasons.append(
                "fiyat fazla kaçmış"
            )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if rsi > previous_rsi:

            score += 15

            reasons.append(
                "RSI yukarı dönüyor"
            )

        if (
            35.0
            <= rsi
            <= 55.0
        ):

            score += 10

            reasons.append(
                "RSI erken momentum bölgesinde"
            )

        if rsi >= RSI_HIGH:

            score -= 20

            reasons.append(
                "RSI aşırı yüksek"
            )

        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

        if histogram > previous_histogram:

            score += 15

            reasons.append(
                "MACD histogram iyileşiyor"
            )

        if macd > macd_signal:

            score += 10

            reasons.append(
                "MACD pozitif"
            )

        # ----------------------------------------------------
        # EMA
        # ----------------------------------------------------

        if ema_fast > ema_slow:

            score += 10

            reasons.append(
                "EMA yapısı pozitif"
            )

        return (
            max(
                0,
                min(
                    score,
                    100,
                ),
            ),
            reasons,
        )


    def _early_curvature(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
        volume: Dict[str, Any],
    ) -> tuple[int, str, list[str]]:

        score = 0

        reasons: list[str] = []

        price = values[
            "price"
        ]

        previous_close = _prev(
            df,
            "close",
            price,
        )

        rsi = values[
            "rsi"
        ]

        previous_rsi = _prev(
            df,
            "rsi",
            rsi,
        )

        previous_rsi_2 = _prev2(
            df,
            "rsi",
            previous_rsi,
        )

        histogram = values[
            "macd_histogram"
        ]

        previous_histogram = _prev(
            df,
            "macd_histogram",
            histogram,
        )

        previous_histogram_2 = _prev2(
            df,
            "macd_histogram",
            previous_histogram,
        )

        ratio = _f(
            volume.get(
                "volume_ratio"
            ),
            0.0,
        )

        acceleration = _f(
            volume.get(
                "volume_acceleration"
            ),
            0.0,
        )

        building = bool(
            volume.get(
                "volume_building"
            )
        )

        # ----------------------------------------------------
        # Fiyat kontrollü
        # ----------------------------------------------------

        price_change = _safe_pct(
            price,
            previous_close,
        )

        if (
            -3.0
            <= price_change
            <= 3.0
        ):

            score += 10

            reasons.append(
                "fiyat kontrollü"
            )

        # ----------------------------------------------------
        # RSI kıvrımı
        # ----------------------------------------------------

        if rsi > previous_rsi:

            score += 15

            reasons.append(
                "RSI kıvrımı başladı"
            )

        if (
            rsi > previous_rsi
            and previous_rsi
            > previous_rsi_2
        ):

            score += 10

            reasons.append(
                "RSI iki mum üst üste yükseliyor"
            )

        # ----------------------------------------------------
        # MACD kıvrımı
        # ----------------------------------------------------

        if histogram > previous_histogram:

            score += 15

            reasons.append(
                "MACD kıvrımı başladı"
            )

        if (
            histogram > previous_histogram
            and previous_histogram
            > previous_histogram_2
        ):

            score += 10

            reasons.append(
                "MACD histogram iyileşiyor"
            )

        # ----------------------------------------------------
        # HACİM kıvrımı
        # ----------------------------------------------------

        if ratio >= MIN_VOLUME_RATIO:

            score += 15

            reasons.append(
                "hacim kıvrımı oluşuyor"
            )

        if acceleration >= MIN_VOLUME_ACCELERATION:

            score += 10

            reasons.append(
                "hacim ivmesi kıvrımı destekliyor"
            )

        if building:

            score += 10

            reasons.append(
                "hacim kademeli yükseliyor"
            )

        # ----------------------------------------------------
        # ÜÇLÜ EŞLEŞME
        # ----------------------------------------------------

        if (
            rsi > previous_rsi
            and histogram > previous_histogram
            and ratio >= MIN_VOLUME_RATIO
        ):

            score += 15

            reasons.append(
                "RSI + MACD + hacim birlikte dönüyor"
            )

        # ----------------------------------------------------
        # KIVRIM TİPİ
        # ----------------------------------------------------

        if score >= 80:

            curvature_type = (
                "KIVRIM ÖNCÜ"
            )

        elif score >= 60:

            curvature_type = (
                "KIVRIM GELİŞİYOR"
            )

        elif score >= 40:

            curvature_type = (
                "KIVRIM ADAYI"
            )

        else:

            curvature_type = "YOK"

        return (
            min(score, 100),
            curvature_type,
            reasons,
                )
    def _final_score(
        self,
        volume_score: int,
        momentum_score: int,
        curvature_score: int,
    ) -> int:

        score = (
            volume_score * 0.55
            + curvature_score * 0.30
            + momentum_score * 0.15
        )

        return int(
            round(
                _clamp(
                    score,
                    0,
                    100,
                )
            )
        )


    def _decide_signal(
        self,
        score: int,
        price_change: float,
        rsi: float,
        volume: Dict[str, Any],
        curvature_type: str,
    ) -> tuple[
        str,
        str,
        bool,
        str,
    ]:

        ratio = _f(
            volume.get(
                "volume_ratio"
            ),
            0.0,
        )

        acceleration = _f(
            volume.get(
                "volume_acceleration"
            ),
            0.0,
        )

        building = bool(
            volume.get(
                "volume_building"
            )
        )

        spike = bool(
            volume.get(
                "volume_spike"
            )
        )

        if ratio < MIN_VOLUME_RATIO:

            return (
                "BEKLE",
                "YOK",
                True,
                "hacim yetersiz",
            )

        if price_change > MAX_CHASE_PRICE_CHANGE:

            return (
                "BEKLE",
                "YOK",
                True,
                "fiyat hareketi fazla ilerledi",
            )

        if rsi >= RSI_HIGH:

            return (
                "BEKLE",
                "YOK",
                True,
                "RSI aşırı yüksek",
            )

        if (
            score >= 78
            and ratio >= STRONG_VOLUME_RATIO
            and (
                acceleration >= 0.10
                or spike
            )
            and price_change
            <= MAX_CHASE_PRICE_CHANGE
        ):

            return (
                "ÇOK GÜÇLÜ AL",
                "HACİM + İVMELENME",
                False,
                "",
            )

        if (
            score >= 65
            and ratio >= AL_VOLUME_RATIO
            and (
                building
                or acceleration
                >= MIN_VOLUME_ACCELERATION
                or curvature_type
                in (
                    "KIVRIM ÖNCÜ",
                    "KIVRIM GELİŞİYOR",
                )
            )
            and price_change
            <= MAX_EARLY_PRICE_CHANGE
        ):

            return (
                "AL",
                "ERKEN HACİM TEYİDİ",
                False,
                "",
            )

        if (
            score >= 70
            and curvature_type
            == "KIVRIM ÖNCÜ"
            and ratio >= MIN_VOLUME_RATIO
            and acceleration
            >= MIN_VOLUME_ACCELERATION
        ):

            return (
                "AL",
                "KIVRIM + HACİM",
                False,
                "",
            )

        if (
            score >= 50
            and curvature_type
            != "YOK"
        ):

            return (
                "BEKLE",
                "İZLE",
                False,
                "",
            )

        return (
            "BEKLE",
            "YOK",
            True,
            "yeterli sinyal oluşmadı",
        )


    def _cooldown_check(
        self,
        symbol: str,
    ) -> bool:

        now = time.time()

        previous = self._last_signals.get(
            symbol
        )

        if previous is None:

            return True

        return (
            now - previous
            >= self.signal_cooldown
        )


    def _mark_signal(
        self,
        symbol: str,
    ) -> None:

        self._last_signals[
            symbol
        ] = time.time()


    def calculate(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> V29Result:

        symbol = (
            str(symbol)
            .upper()
            .strip()
        )

        result = V29Result(
            symbol=symbol
        )

        if (
            df is None
            or df.empty
        ):

            result.rejected = True

            result.reject_reason = (
                "veri yok"
            )

            return result

        if len(df) < 30:

            result.rejected = True

            result.reject_reason = (
                "yeterli mum verisi yok"
            )

            return result

        work = self.prepare_dataframe(
            df
        )

        if work.empty:

            result.rejected = True

            result.reject_reason = (
                "dataframe hazırlanamadı"
            )

            return result

        values = self._extract_values(
            work
        )

        result.price = values[
            "price"
        ]

        result.price_change = values[
            "price_change"
        ]

        result.rsi = values[
            "rsi"
        ]

        result.macd = values[
            "macd"
        ]

        result.macd_signal = values[
            "macd_signal"
        ]

        result.macd_histogram = values[
            "macd_histogram"
        ]

        result.ema_fast = values[
            "ema_fast"
        ]

        result.ema_slow = values[
            "ema_slow"
        ]

        volume = self._volume_analysis(
            work
        )

        result.volume_try = _f(
            volume.get(
                "volume_try"
            )
        )

        result.average_volume_try = _f(
            volume.get(
                "average_volume_try"
            )
        )

        result.volume_ratio = _f(
            volume.get(
                "volume_ratio"
            )
        )

        result.volume_change_pct = _f(
            volume.get(
                "volume_change_pct"
            )
        )

        result.volume_acceleration = _f(
            volume.get(
                "volume_acceleration"
            )
        )

        result.volume_building = bool(
            volume.get(
                "volume_building"
            )
        )

        result.volume_spike = bool(
            volume.get(
                "volume_spike"
            )
        )

        (
            volume_score,
            volume_reasons,
        ) = self._volume_score(
            volume
        )

        (
            momentum_score,
            momentum_reasons,
        ) = self._momentum_filter(
            work,
            values,
        )

        (
            curvature_score,
            curvature_type,
            curvature_reasons,
        ) = self._early_curvature(
            work,
            values,
            volume,
        )

        result.score = self._final_score(
            volume_score=volume_score,
            momentum_score=momentum_score,
            curvature_score=curvature_score,
        )

        if result.score >= 80:

            result.quality = "ÇOK GÜÇLÜ"

        elif result.score >= 70:

            result.quality = "GÜÇLÜ"

        elif result.score >= 60:

            result.quality = "İYİ"

        elif result.score >= 50:

            result.quality = "ADAY"

        else:

            result.quality = "ZAYIF"

        result.reasons = []

        result.reasons.extend(
            volume_reasons
        )

        result.reasons.extend(
            momentum_reasons
        )

        result.reasons.extend(
            curvature_reasons
        )

        result.reasons = list(
            dict.fromkeys(
                result.reasons
            )
        )

        (
            signal,
            confirmation,
            rejected,
            reject_reason,
        ) = self._decide_signal(
            score=result.score,
            price_change=result.price_change,
            rsi=result.rsi,
            volume=volume,
            curvature_type=curvature_type,
        )

        result.signal = signal

        result.confirmation = (
            confirmation
        )

        result.rejected = rejected

        result.reject_reason = (
            reject_reason
        )

        if result.signal in (
            "AL",
            "ÇOK GÜÇLÜ AL",
        ):

            if not self._cooldown_check(
                symbol
            ):

                result.signal = "BEKLE"

                result.confirmation = (
                    "COOLDOWN"
                )

                result.rejected = True

                result.reject_reason = (
                    "aynı coin için tekrar "
                    "sinyal bekleme süresi"
                )

            else:

                self._mark_signal(
                    symbol
                )

        return result


    def analyze(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> Dict[str, Any]:

        result = self.calculate(
            symbol,
            df,
        )

        return result.to_dict()


v29_engine = V29Engine()


def calculate_v29_signal(
    symbol: str,
    df: pd.DataFrame,
) -> Dict[str, Any]:

    return v29_engine.analyze(
        symbol,
        df,
    )


def calculate_signal(
    symbol: str,
    df: pd.DataFrame,
) -> Dict[str, Any]:

    return calculate_v29_signal(
        symbol,
        df,
    )


def debug_v29(
    symbol: str,
    df: pd.DataFrame,
) -> None:

    result = calculate_v29_signal(
        symbol,
        df,
    )

    print()
    print("=" * 64)
    print(
        f"🐋 BALİNA RADARI {V29_VERSION}"
    )
    print("=" * 64)

    print(
        f"🪙 Coin       : "
        f"{result['symbol']}"
    )

    print(
        f"💰 Fiyat      : "
        f"{result['price']}"
    )

    print(
        f"🎯 Skor       : "
        f"{result['score']}/100"
    )

    print(
        f"🟢 Sinyal     : "
        f"{result['signal']}"
    )

    print(
        f"🌀 Kıvrım     : "
        f"{result['curvature_type']}"
    )

    print(
        f"📈 RSI        : "
        f"{result['rsi']:.2f}"
    )

    print(
        f"📊 Hacim      : "
        f"{result['volume_ratio']:.2f}x"
    )

    print(
        f"🚀 Hacim İvme : "
        f"{result['volume_acceleration'] * 100:.2f}%"
    )

    print(
        f"⭐ Kalite     : "
        f"{result['quality']}"
    )

    if result["rejected"]:

        print(
            f"⛔ Red        : "
            f"{result['reject_reason']}"
        )

    print()
    print("📋 NEDENLER:")

    reasons = result.get(
        "reasons",
        [],
    )

    if reasons:

        for reason in reasons:

            print(
                f"  • {reason}"
            )

    else:

        print(
            "  • Henüz yeterli neden oluşmadı."
        )

    print("=" * 64)
    print()
