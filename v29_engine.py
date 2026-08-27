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
        # KOLON İSİMLERİ
        # ----------------------------------------------------

        work.columns = [
            str(column)
            .strip()
            .lower()
            for column in work.columns
        ]

        # ----------------------------------------------------
        # SAYISAL KOLONLAR
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
        # FİYAT VERİSİ
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

            rsi_series = (
                100
                - (
                    100
                    / (1 + rs)
                )
            )

            # ------------------------------------------------
            # RSI TEMİZLEME
            # ------------------------------------------------

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

            work["rsi"] = rsi_series


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

        previous_close = _prev(
            df,
            "close",
            close,
        )

        price_change = _safe_pct(
            close,
            previous_close,
        )

        # ----------------------------------------------------
        # HACİM
        # ----------------------------------------------------

        volume_try = _last(
            df,
            "quote_volume",
            0.0,
        )

        if volume_try <= 0:
            volume_try = _last(
                df,
                "volume",
                0.0,
            )

        average_volume_try = 0.0

        if "quote_volume" in df.columns:

            average_volume_try = _f(
                df["quote_volume"]
                .rolling(
                    20,
                    min_periods=1,
                )
                .mean()
                .iloc[-1],
                0.0,
            )

        elif "volume" in df.columns:

            average_volume_try = _f(
                df["volume"]
                .rolling(
                    20,
                    min_periods=1,
                )
                .mean()
                .iloc[-1],
                0.0,
            )

        if average_volume_try > 0:

            volume_ratio = (
                volume_try
                / average_volume_try
            )

        else:

            volume_ratio = 1.0

        previous_volume = _prev(
            df,
            "quote_volume",
            0.0,
        )

        if previous_volume <= 0:

            previous_volume = _prev(
                df,
                "volume",
                volume_try,
            )

        volume_change_pct = _safe_pct(
            volume_try,
            previous_volume,
        )

        volume_acceleration = (
            volume_change_pct
            / 100.0
        )

        volume_building = (
            volume_ratio >= MIN_VOLUME_RATIO
            and volume_acceleration
            >= MIN_VOLUME_ACCELERATION
        )

        volume_spike = (
            volume_ratio
            >= SPIKE_VOLUME_RATIO
        )

        return {
            "price": close,
            "price_change": price_change,
            "rsi": rsi,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_histogram": histogram,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "volume_try": volume_try,
            "average_volume_try": average_volume_try,
            "volume_ratio": volume_ratio,
            "volume_change_pct": volume_change_pct,
            "volume_acceleration": volume_acceleration,
            "volume_building": float(
                volume_building
            ),
            "volume_spike": float(
                volume_spike
            ),
        }


    # ========================================================
    # TREND SKORU
    # ========================================================

    def _trend_score(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0

        reasons: list[str] = []

        price = values["price"]

        ema_fast = values["ema_fast"]

        ema_slow = values["ema_slow"]

        previous_ema_fast = _prev(
            df,
            "ema_9",
            ema_fast,
        )

        previous_ema_slow = _prev(
            df,
            "ema_20",
            ema_slow,
        )

        if price > ema_fast:

            score += 10

            reasons.append(
                "fiyat kısa EMA üzerinde"
            )

        if ema_fast > previous_ema_fast:

            score += 8

            reasons.append(
                "kısa EMA yukarı dönüyor"
            )

        if price > ema_slow:

            score += 5

            reasons.append(
                "fiyat orta EMA üzerinde"
            )

        if ema_fast > ema_slow:

            score += 7

            reasons.append(
                "EMA yapısı pozitif"
            )

        elif ema_fast > previous_ema_slow:

            score += 4

            reasons.append(
                "EMA sıkışması / erken dönüş"
            )

        return (
            min(score, 30),
            reasons,
        )


    # ========================================================
    # MOMENTUM
    # ========================================================

    def _momentum_score(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0

        reasons: list[str] = []

        rsi = values["rsi"]

        previous_rsi = _prev(
            df,
            "rsi",
            rsi,
        )

        macd = values["macd"]

        macd_signal = values[
            "macd_signal"
        ]

        histogram = values[
            "macd_histogram"
        ]

        previous_histogram = _prev(
            df,
            "macd_histogram",
            histogram,
        )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if rsi > previous_rsi:

            score += 8

            reasons.append(
                "RSI toparlanıyor"
            )

        if 30 <= rsi <= 42:

            score += 8

            reasons.append(
                "RSI dipten dönüş bölgesinde"
            )

        elif 42 < rsi < 50:

            score += 6

            reasons.append(
                "RSI momentum bölgesine yaklaşıyor"
            )

        elif 50 <= rsi < RSI_HIGH:

            score += 3

            reasons.append(
                "RSI pozitif momentumda"
            )

        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

        if histogram > previous_histogram:

            score += 8

            reasons.append(
                "MACD histogram iyileşiyor"
            )

        if macd > macd_signal:

            score += 7

            reasons.append(
                "MACD pozitif"
            )

        elif histogram > previous_histogram:

            score += 6

            reasons.append(
                "MACD kesişim öncesi toparlanıyor"
            )

        return (
            min(score, 30),
            reasons,
        )


    # ========================================================
    # HACİM SKORU
    # ========================================================

    def _volume_score(
        self,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0

        reasons: list[str] = []

        ratio = values[
            "volume_ratio"
        ]

        acceleration = values[
            "volume_acceleration"
        ]

        if ratio >= 1.10:

            score += 8

            reasons.append(
                "hacim normalin üzerinde"
            )

        if ratio >= 1.25:

            score += 5

            reasons.append(
                "hacim belirgin şekilde artmış"
            )

        if ratio >= 1.50:

            score += 5

            reasons.append(
                "yüksek hacim aktivitesi"
            )

        if acceleration >= 0.05:

            score += 5

            reasons.append(
                "hacim ivmesi pozitif"
            )

        if acceleration >= 0.15:

            score += 4

            reasons.append(
                "hacim ivmesi güçlü"
            )

        return (
            min(score, 25),
            reasons,
        )


    # ========================================================
    # KIVRIM
    # ========================================================

    def _curvature_score(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
    ) -> tuple[
        int,
        str,
        int,
        list[str],
    ]:

        score = 0

        early = 0

        reasons: list[str] = []

        price = values["price"]

        rsi = values["rsi"]

        previous_close = _prev(
            df,
            "close",
            price,
        )

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

        volume_ratio = values[
            "volume_ratio"
        ]

        volume_acceleration = values[
            "volume_acceleration"
        ]

        price_change = values[
            "price_change"
        ]

        # ----------------------------------------------------
        # FİYAT HENÜZ KAÇMAMIŞ
        # ----------------------------------------------------

        if (
            -4.5
            <= price_change
            <= 4.5
        ):

            early += 10

        # ----------------------------------------------------
        # RSI DÖNÜŞÜ
        # ----------------------------------------------------

        if rsi > previous_rsi:

            score += 15

            early += 10

            reasons.append(
                "RSI yön değiştirdi"
            )

        if (
            rsi > previous_rsi
            and previous_rsi
            > previous_rsi_2
        ):

            score += 5

            early += 5

            reasons.append(
                "RSI iki mumdur toparlanıyor"
            )

        # ----------------------------------------------------
        # MACD DÖNÜŞÜ
        # ----------------------------------------------------

        if histogram > previous_histogram:

            score += 15

            early += 10

            reasons.append(
                "MACD histogram yön değiştirdi"
            )

        if (
            histogram > previous_histogram
            and previous_histogram
            > previous_histogram_2
        ):

            score += 5

            early += 5

            reasons.append(
                "MACD histogram iki mumdur iyileşiyor"
            )

        # ----------------------------------------------------
        # HACİM
        # ----------------------------------------------------

        if volume_ratio >= 1.10:

            score += 10

            early += 5

            reasons.append(
                "hacim kıvrımı destekliyor"
            )

        if volume_acceleration >= 0.05:

            score += 10

            early += 5

            reasons.append(
                "hacim ivmesi kıvrımı destekliyor"
            )

        # ----------------------------------------------------
        # FİYAT TOPARLANMASI
        # ----------------------------------------------------

        if price > previous_close:

            score += 10

            reasons.append(
                "fiyat toparlanıyor"
            )

        # ----------------------------------------------------
        # ANA ERKENLİK BONUSU
        # ----------------------------------------------------

        if (
            rsi > previous_rsi
            and histogram > previous_histogram
            and volume_ratio >= 1.05
        ):

            score += 10

            early += 10

            reasons.append(
                "RSI + MACD + hacim aynı anda dönüyor"
            )

        # ----------------------------------------------------
        # KIVRIM TİPİ
        # ----------------------------------------------------

        if (
            score >= 60
            and early >= 35
        ):

            curvature_type = (
                "KIVRIM ÖNCÜ"
            )

        elif (
            score >= 40
            and early >= 20
        ):

            curvature_type = (
                "KIVRIM GELİŞİYOR"
            )

        elif score >= 25:

            curvature_type = (
                "KIVRIM ADAYI"
            )

        else:

            curvature_type = "YOK"

        return (
            min(score, 100),
            curvature_type,
            min(early, 100),
            reasons,
        )
