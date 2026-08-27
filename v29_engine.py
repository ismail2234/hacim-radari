from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict
import math
import time

import pandas as pd

from indicators import add_indicators
from volume_engine import calculate_volume_metrics


V29_VERSION = "V29"


# ============================================================
# V29 YENİ KARAR MANTIĞI
# ============================================================
#
# ANA ÖNCELİK:
#     HACİM
#
# Sinyaller SADECE:
#
#     AL
#     ÇOK GÜÇLÜ AL
#     BEKLE
#
# RSI / MACD / EMA:
#     Ana sinyal üretmez.
#     Sadece erken hareketi filtrelemeye yardımcı olur.
#
# Temel fikir:
#
#     hacim yavaş yavaş yükseliyor
#             ↓
#     hacim belirgin şekilde güçleniyor
#             ↓
#     fiyat henüz kaçmamış
#             ↓
#     AL
#
#     hacim patlıyor
#             ↓
#     fiyat yukarı tepki veriyor
#             ↓
#     ÇOK GÜÇLÜ AL
#
# ============================================================


# ============================================================
# HACİM EŞİKLERİ
# ============================================================

MIN_VOLUME_RATIO = 1.05

AL_VOLUME_RATIO = 1.10

STRONG_VOLUME_RATIO = 2.00

SPIKE_VOLUME_RATIO = 3.00

MIN_VOLUME_ACCELERATION = 0.05


# ============================================================
# FİYAT FİLTRESİ
# ============================================================

# Hareket başlamış ama henüz fazla kaçmamış
# bölgeyi hedefliyoruz.

MAX_EARLY_PRICE_CHANGE = 3.5

MAX_CHASE_PRICE_CHANGE = 7.0


# ============================================================
# RSI FİLTRESİ
# ============================================================

RSI_HIGH = 70.0


# ============================================================
# TEKRAR SİNYAL
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

        work.columns = [
            str(column)
            .strip()
            .lower()
            for column in work.columns
        ]

        # ----------------------------------------------------
        # Fiyat kolonları
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
        # Gerekli fiyat verisi
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

        # ----------------------------------------------------
        # Mevcut indikatör motoru
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

            avg_gain = gain.ewm(
                alpha=1 / 14,
                adjust=False,
            ).mean()

            avg_loss = loss.ewm(
                alpha=1 / 14,
                adjust=False,
            ).mean()

            avg_loss = (
                avg_loss.replace(
                    0,
                    pd.NA,
                )
            )

            rs = (
                avg_gain
                / avg_loss
            )

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
    # HACİM MOTORU
    # ========================================================

    def _volume_analysis(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, Any]:

        """
        Yeni volume_engine.py ile konuşan
        ana hacim katmanı.
        """

        try:

            metrics = (
                calculate_volume_metrics(
                    df
                )
            )

        except Exception:

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
            }

        return metrics

    # ========================================================
    # HACİM KARARINI BELİRLE
    # ========================================================

    def _volume_signal(
        self,
        metrics: Dict[str, Any],
        values: Dict[str, float],
    ) -> str:

        ratio = _f(
            metrics.get(
                "volume_ratio"
            )
        )

        acceleration = _f(
            metrics.get(
                "volume_acceleration"
            )
        )

        price_change = _f(
            values.get(
                "price_change"
            )
        )

        bullish = bool(
            metrics.get(
                "bullish_candle",
                False,
            )
        )

        volume_building = bool(
            metrics.get(
                "volume_building",
                False,
            )
        )

        volume_trend_up = bool(
            metrics.get(
                "volume_trend_up",
                False,
            )
        )

        # ====================================================
        # ÇOK GÜÇLÜ AL
        # ====================================================

        if (
            ratio >= STRONG_VOLUME_RATIO
            and bullish
            and price_change > 0
        ):

            return "ÇOK GÜÇLÜ AL"

        if (
            ratio >= SPIKE_VOLUME_RATIO
            and price_change >= 0
        ):

            return "ÇOK GÜÇLÜ AL"

        # ====================================================
        # AL
        # ====================================================

        if (
            volume_building
            and ratio >= MIN_VOLUME_RATIO
            and 0 < price_change
            <= MAX_EARLY_PRICE_CHANGE
        ):

            return "AL"

        if (
            volume_trend_up
            and acceleration >= MIN_VOLUME_ACCELERATION
            and 0 < price_change
            <= MAX_EARLY_PRICE_CHANGE
        ):

            return "AL"

        return "YOK"
    # ========================================================
    # TEKNİK FİLTRE
    # ========================================================

    def _technical_filter(
        self,
        df: pd.DataFrame,
        values: Dict[str, float],
    ) -> tuple[bool, list[str]]:

        """
        Teknik göstergeler burada ana sinyal değildir.

        Sadece hacim sinyalinin çok zayıf / tehlikeli
        durumda olup olmadığını kontrol eder.
        """

        reasons: list[str] = []

        rsi = values["rsi"]

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

        ema_fast = values[
            "ema_fast"
        ]

        ema_slow = values[
            "ema_slow"
        ]

        # ----------------------------------------------------
        # RSI aşırı şişmişse yeni hareket kovalanmaz.
        # ----------------------------------------------------

        if rsi >= RSI_HIGH:

            reasons.append(
                "RSI yüksek — fiyat kovalanmıyor"
            )

            return False, reasons

        # ----------------------------------------------------
        # MACD histogram düşüyorsa dikkat.
        # ----------------------------------------------------

        if histogram < previous_histogram:

            reasons.append(
                "MACD histogram zayıflıyor"
            )

        else:

            reasons.append(
                "MACD histogram destekliyor"
            )

        # ----------------------------------------------------
        # EMA yönü
        # ----------------------------------------------------

        if ema_fast >= ema_slow:

            reasons.append(
                "EMA yapısı olumlu"
            )

        else:

            # EMA henüz negatif olsa bile
            # hacim sinyalini tamamen öldürmüyoruz.
            reasons.append(
                "EMA henüz tam dönmedi"
            )

        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

        if macd > macd_signal:

            reasons.append(
                "MACD pozitif"
            )

        elif histogram > previous_histogram:

            reasons.append(
                "MACD erken toparlanıyor"
            )

        else:

            reasons.append(
                "MACD henüz teyit vermiyor"
            )

        return True, reasons


    # ========================================================
    # SKOR
    # ========================================================

    def _calculate_score(
        self,
        metrics: Dict[str, Any],
        values: Dict[str, float],
    ) -> int:

        """
        Skor artık sinyalin patronu değildir.

        Sadece analiz/debug amacıyla tutulur.

        HACİM en yüksek ağırlığa sahiptir.
        """

        ratio = _f(
            metrics.get(
                "volume_ratio"
            )
        )

        acceleration = _f(
            metrics.get(
                "volume_acceleration"
            )
        )

        price_change = _f(
            values.get(
                "price_change"
            )
        )

        bullish = bool(
            metrics.get(
                "bullish_candle",
                False,
            )
        )

        building = bool(
            metrics.get(
                "volume_building",
                False,
            )
        )

        score = 0

        # ----------------------------------------------------
        # HACİM
        # ----------------------------------------------------

        if ratio >= 1.05:
            score += 25

        if ratio >= 1.25:
            score += 15

        if ratio >= 1.50:
            score += 15

        if ratio >= 2.00:
            score += 20

        if ratio >= 3.00:
            score += 10

        # ----------------------------------------------------
        # HACİM İVMESİ
        # ----------------------------------------------------

        if acceleration >= 0.05:
            score += 10

        if acceleration >= 0.15:
            score += 10

        # ----------------------------------------------------
        # HACİM HAZIRLANIYOR
        # ----------------------------------------------------

        if building:
            score += 10

        # ----------------------------------------------------
        # FİYAT
        # ----------------------------------------------------

        if bullish:
            score += 5

        if 0 < price_change <= 3.5:
            score += 5

        return int(
            _clamp(
                score,
                0,
                100,
            )
        )


    # ========================================================
    # KALİTE
    # ========================================================

    def _quality(
        self,
        signal: str,
    ) -> str:

        if signal == "ÇOK GÜÇLÜ AL":

            return "ÇOK GÜÇLÜ"

        if signal == "AL":

            return "GÜÇLÜ"

        return "ZAYIF"


    # ========================================================
    # COOLDOWN
    # ========================================================

    def _cooldown_check(
        self,
        symbol: str,
    ) -> bool:

        now = time.time()

        previous = (
            self._last_signals.get(
                symbol
            )
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


    # ========================================================
    # ANA HESAPLAMA
    # ========================================================

    def calculate(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> V29Result:

        symbol = str(
            symbol
        ).upper().strip()

        result = V29Result(
            symbol=symbol
        )

        # ----------------------------------------------------
        # VERİ KONTROLÜ
        # ----------------------------------------------------

        if (
            df is None
            or df.empty
        ):

            result.rejected = True

            result.reject_reason = (
                "veri yok"
            )

            return result

        if len(df) < 20:

            result.rejected = True

            result.reject_reason = (
                "yeterli mum verisi yok"
            )

            return result

        # ----------------------------------------------------
        # DATAFRAME
        # ----------------------------------------------------

        work = self.prepare_dataframe(
            df
        )

        if work.empty:

            result.rejected = True

            result.reject_reason = (
                "dataframe hazırlanamadı"
            )

            return result

        # ----------------------------------------------------
        # TEKNİK DEĞERLER
        # ----------------------------------------------------

        values = (
            self._extract_values(
                work
            )
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

        # ----------------------------------------------------
        # YENİ TL HACİM MOTORU
        # ----------------------------------------------------

        metrics = (
            self._volume_analysis(
                work
            )
        )

        result.volume_try = _f(
            metrics.get(
                "volume_try"
            )
        )

        result.average_volume_try = _f(
            metrics.get(
                "average_volume_try"
            )
        )

        result.volume_ratio = _f(
            metrics.get(
                "volume_ratio"
            )
        )

        result.volume_change_pct = _f(
            metrics.get(
                "volume_change_pct"
            )
        )

        result.volume_acceleration = _f(
            metrics.get(
                "volume_acceleration"
            )
        )

        result.volume_building = bool(
            metrics.get(
                "volume_building",
                False,
            )
        )

        result.volume_spike = bool(
            metrics.get(
                "volume_spike",
                False,
            )
        )

        # ====================================================
        # HACİM SİNYALİ
        # ====================================================

        volume_signal = (
            self._volume_signal(
                metrics,
                values,
            )
        )

        # ====================================================
        # TEKNİK FİLTRE
        # ====================================================

        technical_ok, technical_reasons = (
            self._technical_filter(
                work,
                values,
            )
        )

        # ====================================================
        # SKOR
        # ====================================================

        result.score = (
            self._calculate_score(
                metrics,
                values,
            )
        )

        # ====================================================
        # NEDENLER
        # ====================================================

        result.reasons = []

        ratio = result.volume_ratio

        acceleration = (
            result.volume_acceleration
        )

        if result.volume_building:

            result.reasons.append(
                "hacim yükseliyor"
            )

        if ratio >= 1.10:

            result.reasons.append(
                "TL hacmi güçleniyor"
            )

        if ratio >= 1.50:

            result.reasons.append(
                "TL hacmi belirgin yükseldi"
            )

        if ratio >= 2.00:

            result.reasons.append(
                "TL hacim patlaması"
            )

        if ratio >= 3.00:

            result.reasons.append(
                "TL hacim çok güçlü patladı"
            )

        if acceleration >= 0.05:

            result.reasons.append(
                "hacim ivmesi pozitif"
            )

        if acceleration >= 0.15:

            result.reasons.append(
                "hacim ivmesi çok güçlü"
            )

        if values["price_change"] > 0:

            result.reasons.append(
                "fiyat yukarı tepki veriyor"
            )

        result.reasons.extend(
            technical_reasons
        )

        result.reasons = list(
            dict.fromkeys(
                result.reasons
            )
        )

        # ====================================================
        # FİNAL KARAR
        # ====================================================
        #
        # ÇOK ÖNEMLİ:
        #
        # Artık score >= X olduğu için AL
        # mantığı kullanılmıyor.
        #
        # HACİM SİNYALİ kararın merkezinde.
        #

        signal = volume_signal

        # ----------------------------------------------------
        # Teknik filtre çok kötü ise AL iptal.
        # ----------------------------------------------------

        if (
            signal == "AL"
            and not technical_ok
        ):

            signal = "BEKLE"

            result.rejected = True

            result.reject_reason = (
                "teknik filtre uygun değil"
            )

        elif (
            signal == "ÇOK GÜÇLÜ AL"
            and not technical_ok
            and result.rsi >= RSI_HIGH
        ):

            signal = "BEKLE"

            result.rejected = True

            result.reject_reason = (
                "RSI çok yüksek, hareket kovalanmıyor"
            )

        # ----------------------------------------------------
        # Fiyat çok fazla kaçtıysa AL yok.
        # ----------------------------------------------------

        if (
            signal in (
                "AL",
                "ÇOK GÜÇLÜ AL",
            )
            and result.price_change
            > MAX_CHASE_PRICE_CHANGE
        ):

            signal = "BEKLE"

            result.rejected = True

            result.reject_reason = (
                "fiyat hareketi fazla ilerledi"
            )

        # ----------------------------------------------------
        # Son karar
        # ----------------------------------------------------

        result.signal = signal

        result.quality = (
            self._quality(
                signal
            )
        )

        if signal == "ÇOK GÜÇLÜ AL":

            result.confirmation = (
                "HACİM PATLAMASI"
            )

            result.rejected = False

            result.reject_reason = ""

        elif signal == "AL":

            result.confirmation = (
                "HACİM YÜKSELİYOR"
            )

            result.rejected = False

            result.reject_reason = ""

        else:

            result.confirmation = "YOK"

        # ====================================================
        # COOLDOWN
        # ====================================================

        if result.signal in (
            "AL",
            "ÇOK GÜÇLÜ AL",
        ):

            if not self._cooldown_check(
                symbol
            ):

                result.signal = (
                    "BEKLE"
                )

                result.confirmation = (
                    "COOLDOWN"
                )

                result.quality = (
                    "ZAYIF"
                )

                result.rejected = True

                result.reject_reason = (
                    "aynı coin için "
                    "tekrar sinyal bekleme süresi"
                )

            else:

                self._mark_signal(
                    symbol
                )

        return result


    # ========================================================
    # KOLAY KULLANIM
    # ========================================================

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


# ============================================================
# GLOBAL ENGINE
# ============================================================

v29_engine = V29Engine()


# ============================================================
# DIŞARIDAN ÇAĞRILABİLECEK FONKSİYON
# ============================================================

def calculate_v29_signal(
    symbol: str,
    df: pd.DataFrame,
) -> Dict[str, Any]:

    return v29_engine.analyze(
        symbol,
        df,
    )


# ============================================================
# GERİYE DÖNÜK UYUMLULUK
# ============================================================

def calculate_signal(
    symbol: str,
    df: pd.DataFrame,
) -> Dict[str, Any]:

    return calculate_v29_signal(
        symbol,
        df,
    )


# ============================================================
# DEBUG
# ============================================================

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
        f"🪙 Coin          : "
        f"{result['symbol']}"
    )

    print(
        f"💰 Fiyat         : "
        f"{result['price']}"
    )

    print(
        f"🎯 Skor          : "
        f"{result['score']}/100"
    )

    print(
        f"📊 TL Hacim      : "
        f"{result['volume_try']:,.2f}"
    )

    print(
        f"📊 Hacim Oranı   : "
        f"{result['volume_ratio']:.2f}x"
    )

    print(
        f"🚀 Hacim İvmesi  : "
        f"{result['volume_acceleration'] * 100:.2f}%"
    )

    print(
        f"📈 RSI           : "
        f"{result['rsi']:.2f}"
    )

    print(
        f"🟢 Sinyal        : "
        f"{result['signal']}"
    )

    print(
        f"✅ Teyit         : "
        f"{result['confirmation']}"
    )

    print(
        f"⭐ Kalite        : "
        f"{result['quality']}"
    )

    if result["rejected"]:

        print(
            f"⛔ Red Nedeni    : "
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
            "  • Henüz sinyal yok."
        )

    print("=" * 64)
    print()


# ============================================================
# DOSYA SONU
# ============================================================
#
# V29 YENİ MANTIK:
#
#       TL HACİM
#          ↓
#     HACİM HAZIRLIĞI
#          ↓
#      FİYAT FİLTRESİ
#          ↓
#     TEKNİK FİLTRE
#          ↓
#    AL / ÇOK GÜÇLÜ AL
#
# Telegram gönderimi bu dosyada yapılmaz.
#
# ============================================================
