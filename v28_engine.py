from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict
import math
import time

import pandas as pd

from indicators import add_indicators
from signal_engine import calculate_signal as calculate_v28_signal


V29_VERSION = "V29"

MIN_SCORE = 60
EARLY_SCORE = 66
STRONG_SCORE = 76

MIN_VOLUME_RATIO = 1.05
MIN_VOLUME_ACCELERATION = 0.05

RSI_OVERSOLD = 30.0
RSI_RECOVERY = 42.0
RSI_MOMENTUM = 50.0
RSI_HIGH = 70.0

DEFAULT_SIGNAL_COOLDOWN = 20 * 60


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace(",", ".")
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(round(_f(value, default)))
    except Exception:
        return default


def _clamp(
    value: float,
    low: float = 0.0,
    high: float = 100.0,
) -> float:
    return max(low, min(high, value))


def _last(
    df: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> float:
    if df is None or df.empty or column not in df.columns:
        return default
    return _f(df[column].iloc[-1], default)


def _prev(
    df: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> float:
    if df is None or len(df) < 2 or column not in df.columns:
        return default
    return _f(df[column].iloc[-2], default)


def _prev2(
    df: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> float:
    if df is None or len(df) < 3 or column not in df.columns:
        return default
    return _f(df[column].iloc[-3], default)


def _safe_pct(
    current: float,
    previous: float,
) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / abs(previous)) * 100.0


@dataclass
class V29Result:
    symbol: str
    score: int = 0
    signal: str = "BEKLE"
    confirmation: str = "YOK"

    curvature_score: int = 0
    curvature_type: str = "YOK"
    early_score: int = 0

    momentum_score: int = 0
    trend_score: int = 0
    volume_score: int = 0
    acceleration_score: int = 0

    price: float = 0.0
    price_change: float = 0.0

    rsi: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0

    ema_fast: float = 0.0
    ema_slow: float = 0.0

    volume_ratio: float = 0.0
    volume_acceleration: float = 0.0

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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class V29Engine:

    def __init__(
        self,
        min_score: int = MIN_SCORE,
        signal_cooldown: int = DEFAULT_SIGNAL_COOLDOWN,
    ) -> None:
        self.min_score = min_score
        self.signal_cooldown = signal_cooldown
        self._last_signals: Dict[str, float] = {}

    def prepare_dataframe(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        if df is None or df.empty:
            return pd.DataFrame()

        work = df.copy()

        work.columns = [
            str(column).strip().lower()
            for column in work.columns
        ]

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

        try:
            result = add_indicators(work)

            if isinstance(
                result,
                pd.DataFrame,
            ):
                work = result

        except TypeError:
            try:
                add_indicators(work)
            except Exception:
                pass

        except Exception:
            pass

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

            rsi_series = (
                100
                - (
                    100
                    / (1 + rs)
                )
            )

            rsi_series = (
                rsi_series
                .infer_objects(copy=False)
                .fillna(50.0)
                .clip(0, 100)
            )

            work["rsi"] = rsi_series

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

        if (
            "macd_histogram" not in work.columns
            and "macd" in work.columns
            and "macd_signal" in work.columns
        ):

            work["macd_histogram"] = (
                work["macd"]
                - work["macd_signal"]
            )

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

            work["v29_volume_ratio"] = (
                work["v29_volume_ratio"]
                .infer_objects(copy=False)
                .fillna(1.0)
            )

            previous_volume = (
                work["volume"].shift(1)
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
                .infer_objects(copy=False)
                .fillna(0.0)
            )

        return work

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
            "price_change": _safe_pct(
                close,
                previous_close,
            ),
        }

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

        return min(score, 30), reasons

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
        macd_signal = values["macd_signal"]
        histogram = values["macd_histogram"]

        previous_histogram = _prev(
            df,
            "macd_histogram",
            histogram,
        )

        if rsi > previous_rsi:
            score += 8
            reasons.append(
                "RSI toparlanıyor"
            )

        if (
            RSI_OVERSOLD
            <= rsi
            <= RSI_RECOVERY
        ):
            score += 8
            reasons.append(
                "RSI dipten dönüş bölgesinde"
            )

        elif (
            RSI_RECOVERY
            < rsi
            < RSI_MOMENTUM
        ):
            score += 6
            reasons.append(
                "RSI momentum bölgesine yaklaşıyor"
            )

        elif (
            RSI_MOMENTUM
            <= rsi
            < RSI_HIGH
        ):
            score += 3
            reasons.append(
                "RSI pozitif momentumda"
            )

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

        elif (
            histogram
            > previous_histogram
        ):
            score += 6
            reasons.append(
                "MACD kesişim öncesi toparlanıyor"
            )

        return min(score, 30), reasons

    def _volume_score(
        self,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0
        reasons: list[str] = []

        ratio = values["volume_ratio"]
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

        return min(score, 25), reasons
