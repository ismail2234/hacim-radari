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


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _last(df: pd.DataFrame, column: str, default: float = 0.0) -> float:
    if df is None or df.empty or column not in df.columns:
        return default
    return _f(df[column].iloc[-1], default)


def _prev(df: pd.DataFrame, column: str, default: float = 0.0) -> float:
    if df is None or len(df) < 2 or column not in df.columns:
        return default
    return _f(df[column].iloc[-2], default)


def _prev2(df: pd.DataFrame, column: str, default: float = 0.0) -> float:
    if df is None or len(df) < 3 or column not in df.columns:
        return default
    return _f(df[column].iloc[-3], default)


def _safe_pct(current: float, previous: float) -> float:
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

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()

        work = df.copy()

        work.columns = [
            str(column).strip().lower()
            for column in work.columns
        ]

        for column in ("open", "high", "low", "close", "volume"):
            if column in work.columns:
                work[column] = pd.to_numeric(
                    work[column],
                    errors="coerce",
                )

        required = [
            column
            for column in ("open", "high", "low", "close")
            if column in work.columns
        ]

        if required:
            work = work.dropna(subset=required)

        work = work.reset_index(drop=True)

        if work.empty:
            return work

        try:
            result = add_indicators(work)
            if isinstance(result, pd.DataFrame):
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
                    .ewm(span=9, adjust=False)
                    .mean()
                )

            if "ema_20" not in work.columns:
                work["ema_20"] = (
                    work["close"]
                    .ewm(span=20, adjust=False)
                    .mean()
                )

            if "ema_50" not in work.columns:
                work["ema_50"] = (
                    work["close"]
                    .ewm(span=50, adjust=False)
                    .mean()
                )

        if "rsi" not in work.columns and "close" in work.columns:

            delta = work["close"].diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)

            avg_gain = gain.ewm(
                alpha=1 / 14,
                adjust=False,
            ).mean()

            avg_loss = loss.ewm(
                alpha=1 / 14,
                adjust=False,
            ).mean()

            avg_loss = avg_loss.replace(0, pd.NA)

            rs = avg_gain / avg_loss

            rsi_series = 100 - (100 / (1 + rs))

            rsi_series = (
                rsi_series
                .infer_objects(copy=False)
                .fillna(50.0)
                .clip(0, 100)
            )

            work["rsi"] = rsi_series

        if "macd" not in work.columns and "close" in work.columns:

            ema12 = (
                work["close"]
                .ewm(span=12, adjust=False)
                .mean()
            )

            ema26 = (
                work["close"]
                .ewm(span=26, adjust=False)
                .mean()
            )

            work["macd"] = ema12 - ema26

            work["macd_signal"] = (
                work["macd"]
                .ewm(span=9, adjust=False)
                .mean()
            )

            work["macd_histogram"] = (
                work["macd"] - work["macd_signal"]
            )

        if (
            "macd_histogram" not in work.columns
            and "macd" in work.columns
            and "macd_signal" in work.columns
        ):
            work["macd_histogram"] = (
                work["macd"] - work["macd_signal"]
            )

        if "volume" in work.columns:

            volume_ma = (
                work["volume"]
                .rolling(20, min_periods=1)
                .mean()
            )

            work["v29_volume_ratio"] = (
                work["volume"]
                / volume_ma.replace(0, pd.NA)
            )

            work["v29_volume_ratio"] = (
                work["v29_volume_ratio"]
                .infer_objects(copy=False)
                .fillna(1.0)
            )

            previous_volume = work["volume"].shift(1)

            work["v29_volume_acceleration"] = (
                work["volume"]
                / previous_volume.replace(0, pd.NA)
            ) - 1.0

            work["v29_volume_acceleration"] = (
                work["v29_volume_acceleration"]
                .replace(
                    [float("inf"), -float("inf")],
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

        close = _last(df, "close")
        rsi = _last(df, "rsi", 50.0)
        macd = _last(df, "macd")
        macd_signal = _last(df, "macd_signal")
        histogram = _last(
            df,
            "macd_histogram",
            macd - macd_signal,
        )

        ema_fast = _last(df, "ema_9", close)
        ema_slow = _last(df, "ema_20", close)

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
            reasons.append("fiyat kısa EMA üzerinde")

        if ema_fast > previous_ema_fast:
            score += 8
            reasons.append("kısa EMA yukarı dönüyor")

        if price > ema_slow:
            score += 5
            reasons.append("fiyat orta EMA üzerinde")

        if ema_fast > ema_slow:
            score += 7
            reasons.append("EMA yapısı pozitif")
        elif ema_fast > previous_ema_slow:
            score += 4
            reasons.append("EMA sıkışması / erken dönüş")

        return min(score, 30), reasons

    def _momentum_score(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0
        reasons: list[str] = []

        rsi = values["rsi"]
        previous_rsi = _prev(df, "rsi", rsi)

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
            reasons.append("RSI toparlanıyor")

        if RSI_OVERSOLD <= rsi <= RSI_RECOVERY:
            score += 8
            reasons.append("RSI dipten dönüş bölgesinde")
        elif RSI_RECOVERY < rsi < RSI_MOMENTUM:
            score += 6
            reasons.append("RSI momentum bölgesine yaklaşıyor")
        elif RSI_MOMENTUM <= rsi < RSI_HIGH:
            score += 3
            reasons.append("RSI pozitif momentumda")

        if histogram > previous_histogram:
            score += 8
            reasons.append("MACD histogram iyileşiyor")

        if macd > macd_signal:
            score += 7
            reasons.append("MACD pozitif")
        elif histogram > previous_histogram:
            score += 6
            reasons.append("MACD kesişim öncesi toparlanıyor")

        return min(score, 30), reasons

    def _volume_score(
        self,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0
        reasons: list[str] = []

        ratio = values["volume_ratio"]
        acceleration = values["volume_acceleration"]

        if ratio >= 1.10:
            score += 8
            reasons.append("hacim normalin üzerinde")

        if ratio >= 1.25:
            score += 5
            reasons.append("hacim belirgin şekilde artmış")

        if ratio >= 1.50:
            score += 5
            reasons.append("yüksek hacim aktivitesi")

        if acceleration >= 0.05:
            score += 5
            reasons.append("hacim ivmesi pozitif")

        if acceleration >= 0.15:
            score += 4
            reasons.append("hacim ivmesi güçlü")

        return min(score, 25), reasons

    def _curvature_score(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
    ) -> tuple[int, str, int, list[str]]:

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

        histogram = values["macd_histogram"]

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

        volume_ratio = values["volume_ratio"]
        volume_acceleration = values["volume_acceleration"]
        price_change = values["price_change"]

        if -4.5 <= price_change <= 4.5:
            early += 10

        if rsi > previous_rsi:
            score += 15
            early += 10
            reasons.append("RSI yön değiştirdi")

        if (
            rsi > previous_rsi
            and previous_rsi > previous_rsi_2
        ):
            score += 5
            early += 5
            reasons.append("RSI iki mumdur toparlanıyor")

        if histogram > previous_histogram:
            score += 15
            early += 10
            reasons.append("MACD histogram yön değiştirdi")

        if (
            histogram > previous_histogram
            and previous_histogram > previous_histogram_2
        ):
            score += 5
            early += 5
            reasons.append("MACD histogram iki mumdur iyileşiyor")

        if volume_ratio >= 1.10:
            score += 10
            early += 5
            reasons.append("hacim kıvrımı destekliyor")

        if volume_acceleration >= 0.05:
            score += 10
            early += 5
            reasons.append("hacim ivmesi kıvrımı destekliyor")

        if price > previous_close:
            score += 10
            reasons.append("fiyat toparlanıyor")

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

        if score >= 60 and early >= 35:
            curvature_type = "KIVRIM ÖNCÜ"
        elif score >= 40 and early >= 20:
            curvature_type = "KIVRIM GELİŞİYOR"
        elif score >= 25:
            curvature_type = "KIVRIM ADAYI"
        else:
            curvature_type = "YOK"

        return (
            min(score, 100),
            curvature_type,
            min(early, 100),
            reasons,
        )

    def _v28_confirmation(
        self,
        df: pd.DataFrame,
    ) -> tuple[str, int, list[str]]:

        try:
            result = calculate_v28_signal(df)
        except Exception:
            return "YOK", 0, []

        if result is None:
            return "YOK", 0, []

        if isinstance(result, dict):

            signal = str(
                result.get("signal")
                or result.get("action")
                or result.get("side")
                or "YOK"
            ).upper()

            score = _i(
                result.get("score"),
                0,
            )

            reasons: list[str] = []

            reason = result.get("reason")

            if reason:
                reasons.append(str(reason))

            return signal, min(score, 100), reasons

        if isinstance(result, str):

            signal = result.upper()

            if "AL" in signal:
                return "AL", 65, ["V28 AL teyidi"]

            return signal, 0, []

        return "YOK", 0, []

    def _market_activity_score(
        self,
        values: Dict[str, float],
    ) -> tuple[int, list[str]]:

        score = 0
        reasons: list[str] = []

        ratio = values["volume_ratio"]
        acceleration = values["volume_acceleration"]
        price_change = values["price_change"]

        if ratio >= 1.10:
            score += 10
            reasons.append("piyasa aktivitesi artıyor")

        if ratio >= 1.30:
            score += 5
            reasons.append("işlem aktivitesi belirgin")

        if acceleration >= 0.05:
            score += 10
            reasons.append("aktivite ivmeleniyor")

        if acceleration >= 0.15:
            score += 5
            reasons.append("aktivite güçlü ivmeleniyor")

        if 0.0 <= price_change <= 3.0:
            score += 5
            reasons.append(
                "aktivite fiyat hareketinden önce geliyor"
            )

        return min(score, 30), reasons

    def _calculate_final_score(
        self,
        trend_score: int,
        momentum_score: int,
        volume_score: int,
        acceleration_score: int,
        curvature_score: int,
        early_score: int,
        v28_score: int,
        market_activity_score: int,
    ) -> int:

        trend_norm = trend_score / 30 * 100
        momentum_norm = momentum_score / 30 * 100
        volume_norm = volume_score / 25 * 100
        acceleration_norm = acceleration_score / 20 * 100

        weighted = (
            trend_norm * 0.12
            + momentum_norm * 0.20
            + volume_norm * 0.15
            + acceleration_norm * 0.10
            + curvature_score * 0.18
            + early_score * 0.10
            + v28_score * 0.05
            + market_activity_score * 0.10
        )

        return int(round(_clamp(weighted)))

    def _quality(
        self,
        score: int,
        curvature_type: str,
        early_score: int,
        volume_ratio: float,
    ) -> str:

        if (
            score >= STRONG_SCORE
            and curvature_type == "KIVRIM ÖNCÜ"
            and early_score >= 45
            and volume_ratio >= 1.15
        ):
            return "ÇOK GÜÇLÜ"

        if (
            score >= 70
            and curvature_type
            in ("KIVRIM ÖNCÜ", "KIVRIM GELİŞİYOR")
        ):
            return "GÜÇLÜ"

        if score >= EARLY_SCORE:
            return "İYİ"

        if score >= MIN_SCORE:
            return "ADAY"

        return "ZAYIF"

    def _decide_signal(
        self,
        score: int,
        curvature_type: str,
        early_score: int,
        rsi: float,
        volume_ratio: float,
        volume_acceleration: float,
        v28_signal: str,
        v28_score: int,
    ) -> tuple[str, str, bool, str]:

        if score < self.min_score:
            return "BEKLE", "YOK", True, "skor eşik altında"

        if curvature_type == "YOK":
            return "BEKLE", "YOK", True, "kıvrım oluşmadı"

        if rsi >= RSI_HIGH and early_score < 50:
            return "BEKLE", "YOK", True, "RSI aşırı yükselmiş"

        if volume_ratio < MIN_VOLUME_RATIO:
            return "BEKLE", "YOK", True, "hacim desteği yetersiz"

        if (
            score >= STRONG_SCORE
            and curvature_type == "KIVRIM ÖNCÜ"
            and early_score >= 45
            and volume_ratio >= 1.10
        ):
            return "AL", "1. TEYİT", False, ""

        if (
            score >= EARLY_SCORE
            and curvature_type
            in ("KIVRIM ÖNCÜ", "KIVRIM GELİŞİYOR")
            and (
                "AL" in v28_signal
                or v28_score >= 60
            )
        ):
            return "AL", "2. TEYİT", False, ""

        if (
            score >= 72
            and curvature_type == "KIVRIM ÖNCÜ"
            and early_score >= 50
            and volume_acceleration >= 0.05
        ):
            return "AL", "V29 ÖNCÜ", False, ""

        if (score >= self.min_score
            and curvature_type == "KIVRIM GELİŞİYOR"
        ):
            return "BEKLE", "İZLE", False, ""

        return "BEKLE", "İZLE", True, "yeterli teyit yok"

    def _cooldown_check(self, symbol: str) -> bool:
        previous = self._last_signals.get(symbol)

        if previous is None:
            return True

        return time.time() - previous >= self.signal_cooldown

    def _mark_signal(self, symbol: str) -> None:
        self._last_signals[symbol] = time.time()

    def calculate(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> V29Result:

        symbol = str(symbol).upper().strip()

        result = V29Result(symbol=symbol)

        if df is None or df.empty:
            result.rejected = True
            result.reject_reason = "veri yok"
            return result

        if len(df) < 30:
            result.rejected = True
            result.reject_reason = "yeterli mum verisi yok"
            return result

        work = self.prepare_dataframe(df)

        if work.empty:
            result.rejected = True
            result.reject_reason = "dataframe hazırlanamadı"
            return result

        values = self._extract_values(work)

        result.price = values["price"]
        result.price_change = values["price_change"]
        result.rsi = values["rsi"]
        result.macd = values["macd"]
        result.macd_signal = values["macd_signal"]
        result.macd_histogram = values["macd_histogram"]
        result.ema_fast = values["ema_fast"]
        result.ema_slow = values["ema_slow"]
        result.volume_ratio = values["volume_ratio"]
        result.volume_acceleration = values["volume_acceleration"]

        trend_score, trend_reasons = self._trend_score(
            work,
            values,
        )

        momentum_score, momentum_reasons = self._momentum_score(
            work,
            values,
        )

        volume_score, volume_reasons = self._volume_score(
            values,
        )

        acceleration_score = 0

        if values["volume_acceleration"] >= 0.05:
            acceleration_score += 10

        if values["volume_acceleration"] >= 0.15:
            acceleration_score += 10

        acceleration_score = min(
            acceleration_score,
            20,
        )

        (
            curvature_score,
            curvature_type,
            early_score,
            curvature_reasons,
        ) = self._curvature_score(
            work,
            values,
        )

        (
            v28_signal,
            v28_score,
            v28_reasons,
        ) = self._v28_confirmation(work)

        (
            market_activity_score,
            activity_reasons,
        ) = self._market_activity_score(values)

        result.trend_score = trend_score
        result.momentum_score = momentum_score
        result.volume_score = volume_score
        result.acceleration_score = acceleration_score
        result.curvature_score = curvature_score
        result.curvature_type = curvature_type
        result.early_score = early_score

        result.score = self._calculate_final_score(
            trend_score=trend_score,
            momentum_score=momentum_score,
            volume_score=volume_score,
            acceleration_score=acceleration_score,
            curvature_score=curvature_score,
            early_score=early_score,
            v28_score=v28_score,
            market_activity_score=market_activity_score,
        )

        result.quality = self._quality(
            score=result.score,
            curvature_type=result.curvature_type,
            early_score=result.early_score,
            volume_ratio=result.volume_ratio,
        )

        result.reasons = list(
            dict.fromkeys(
                trend_reasons
                + momentum_reasons
                + volume_reasons
                + curvature_reasons
                + activity_reasons
                + v28_reasons
            )
        )

        (
            signal,
            confirmation,
            rejected,
            reject_reason,
        ) = self._decide_signal(
            score=result.score,
            curvature_type=result.curvature_type,
            early_score=result.early_score,
            rsi=result.rsi,
            volume_ratio=result.volume_ratio,
            volume_acceleration=result.volume_acceleration,
            v28_signal=v28_signal,
            v28_score=v28_score,
        )

        result.signal = signal
        result.confirmation = confirmation
        result.rejected = rejected
        result.reject_reason = reject_reason

        if result.signal == "AL":

            if not self._cooldown_check(symbol):

                result.signal = "BEKLE"
                result.confirmation = "COOLDOWN"
                result.rejected = True
                result.reject_reason = (
                    "aynı coin için tekrar sinyal bekleme süresi"
                )

            else:
                self._mark_signal(symbol)

        return result

    def analyze(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> Dict[str, Any]:

        return self.calculate(
            symbol,
            df,
        ).to_dict()


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

    print("=" * 60)
    print(f"BALİNA RADARI {V29_VERSION}")
    print("=" * 60)
    print(f"Coin: {result['symbol']}")
    print(f"Fiyat: {result['price']}")
    print(f"Skor: {result['score']}/100")
    print(f"Kıvrım: {result['curvature_type']}")
    print(f"Erkenlik: {result['early_score']}/100")
    print(f"RSI: {result['rsi']:.2f}")
    print(f"Hacim: {result['volume_ratio']:.2f}x")
    print(
        f"Hacim İvmesi: "
        f"{result['volume_acceleration'] * 100:.2f}%"
    )
    print(f"Sinyal: {result['signal']}")
    print(f"Teyit: {result['confirmation']}")
    print(f"Kalite: {result['quality']}")

    if result["rejected"]:
        print(f"Red: {result['reject_reason']}")

    for reason in result.get("reasons", []):
        print(f"- {reason}")

    print("=" * 60)
