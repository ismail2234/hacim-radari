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
    # ========================================================
    # TREND ANALİZİ
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

        # Fiyat kısa EMA üzerine dönüyor.
        if price > ema_fast:

            score += 10

            reasons.append(
                "fiyat kısa EMA üzerinde"
            )

        # Kısa EMA yukarı dönüyor.
        if ema_fast > previous_ema_fast:

            score += 8

            reasons.append(
                "kısa EMA yukarı dönüyor"
            )

        # Fiyat orta EMA üzerinde.
        if price > ema_slow:

            score += 5

            reasons.append(
                "fiyat orta EMA üzerinde"
            )

        # EMA yapısı pozitif.
        if ema_fast > ema_slow:

            score += 7

            reasons.append(
                "EMA yapısı pozitif"
            )

        # Henüz tam trend oluşmamış olabilir.
        # Erken KIVRIM için sıkışma bölgesine
        # küçük puan veriyoruz.
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
    # MOMENTUM ANALİZİ
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

        # RSI yükseliyorsa dipten dönüş açısından
        # önemli bir erken işarettir.
        if rsi > previous_rsi:

            score += 8

            reasons.append(
                "RSI toparlanıyor"
            )

        # Aşırı satımdan dönüş.
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

        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

        # Histogram önceki mumdan daha iyi.
        if histogram > previous_histogram:

            score += 8

            reasons.append(
                "MACD histogram iyileşiyor"
            )

        # MACD sinyal çizgisini geçti.
        if macd > macd_signal:

            score += 7

            reasons.append(
                "MACD pozitif"
            )

        # Henüz kesişmemiş fakat histogram toparlanıyor.
        # Bu bölüm V29'un erkenlik tarafı.
        elif (
            macd <= macd_signal
            and histogram > previous_histogram
        ):

            score += 6

            reasons.append(
                "MACD kesişim öncesi toparlanıyor"
            )

        return (
            min(score, 30),
            reasons,
        )


    # ========================================================
    # HACİM ANALİZİ
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

        # ----------------------------------------------------
        # Hacim normalden yüksek.
        # ----------------------------------------------------

        if ratio >= 1.10:

            score += 8

            reasons.append(
                "hacim normalin üzerinde"
            )

        # Belirgin hacim artışı.
        if ratio >= 1.25:

            score += 5

            reasons.append(
                "hacim belirgin şekilde artmış"
            )

        # Güçlü hacim aktivitesi.
        if ratio >= 1.50:

            score += 5

            reasons.append(
                "yüksek hacim aktivitesi"
            )

        # ----------------------------------------------------
        # Hacim ivmesi.
        # ----------------------------------------------------

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
    # KIVRIM ANALİZİ
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

        """
        V29 KIVRIM motoru.

        Buradaki temel fikir:

        Fiyat henüz çok yükselmemiş olabilir.

        Fakat aynı anda:

            RSI ↑
            MACD histogram ↑
            Hacim ↑
            Hacim ivmesi ↑
            Fiyat dipten toparlanıyor

        ise hareketin başlangıç bölgesinde
        olma ihtimalini artırır.

        """

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

        # ====================================================
        # 1 — FİYAT HENÜZ KAÇMAMIŞ MI?
        # ====================================================

        # Burada amaç zaten yükselmiş coini kovalamamak.
        #
        # Son mumdaki hareket:
        #   - küçük / kontrollü ise erkenlik puanı
        #   - aşırı ise puan verilmez.

        if (
            -4.5
            <= price_change
            <= 4.5
        ):

            early += 10

        # ====================================================
        # 2 — RSI YÖN DEĞİŞİMİ
        # ====================================================

        rsi_turn = (
            rsi > previous_rsi
        )

        if rsi_turn:

            score += 15
            early += 10

            reasons.append(
                "RSI yön değiştirdi"
            )

        # İki mum üst üste toparlanıyorsa
        # daha değerli erken sinyal.
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

        # ====================================================
        # 3 — MACD HISTOGRAM DÖNÜŞÜ
        # ====================================================

        macd_turn = (
            histogram
            > previous_histogram
        )

        if macd_turn:

            score += 15
            early += 10

            reasons.append(
                "MACD histogram yön değiştirdi"
            )

        # İki mum üst üste iyileşme.
        if (
            histogram
            > previous_histogram
            and previous_histogram
            > previous_histogram_2
        ):

            score += 5
            early += 5

            reasons.append(
                "MACD histogram iki mumdur iyileşiyor"
            )

        # ====================================================
        # 4 — HACİM DEĞİŞİMİ
        # ====================================================

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

        # ====================================================
        # 5 — FİYAT TOPARLANIYOR MU?
        # ====================================================

        if price > previous_close:

            score += 10

            reasons.append(
                "fiyat toparlanıyor"
            )

        # ====================================================
        # 6 — ERKENLİK BONUSU
        # ====================================================

        #
        # RSI + MACD + hacim aynı anda dönüyorsa
        # bu bizim için çok önemli.
        #

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

        # ====================================================
        # KIVRIM TİPİ
        # ====================================================

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


    # ========================================================
    # V28 TEYİDİ
    # ========================================================

    def _v28_confirmation(
        self,
        df: pd.DataFrame,
    ) -> tuple[
        str,
        int,
        list[str],
    ]:

        """
        V28 motorunu yardımcı teyit olarak kullanır.

        V29, V28'i tamamen silmez.

        Güçlü V28 sinyali varsa V29 bunu
        ek teyit olarak kullanabilir.
        """

        try:

            result = calculate_v28_signal(
                df
            )

        except Exception:

            return (
                "YOK",
                0,
                [],
            )

        if result is None:

            return (
                "YOK",
                0,
                [],
            )

        # ----------------------------------------------------
        # Dictionary sonuç.
        # ----------------------------------------------------

        if isinstance(
            result,
            dict,
        ):

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

            reason = result.get(
                "reason"
            )

            if reason:

                reasons.append(
                    str(reason)
                )

            return (
                signal,
                min(score, 100),
                reasons,
            )

        # ----------------------------------------------------
        # String sonuç.
        # ----------------------------------------------------

        if isinstance(
            result,
            str,
        ):

            signal = result.upper()

            if "AL" in signal:

                return (
                    "AL",
                    65,
                    ["V28 AL teyidi"],
                )

            return (
                signal,
                0,
                [],
            )

        return (
            "YOK",
            0,
            [],
        )


    # ========================================================
    # HUBAI BENZERİ PİYASA AKTİVİTESİ
    # ========================================================

    def _market_activity_score(
        self,
        values: Dict[str, float],
    ) -> tuple[
        int,
        list[str],
    ]:

        """
        HubAI Trader yaklaşımından ilhamlanan
        piyasa aktivitesi katmanı.

        Burada dışarıdan sahte veri kullanılmaz.

        Elimizdeki gerçek Binance verilerinden:

            • hacim
            • hacim oranı
            • hacim ivmesi
            • fiyat hareketi

        değerlendirilir.
        """

        score = 0

        reasons: list[str] = []

        ratio = values[
            "volume_ratio"
        ]

        acceleration = values[
            "volume_acceleration"
        ]

        price_change = values[
            "price_change"
        ]

        # Aktivite artıyor.
        if ratio >= 1.10:

            score += 10

            reasons.append(
                "piyasa aktivitesi artıyor"
            )

        # Belirgin aktivite.
        if ratio >= 1.30:

            score += 5

            reasons.append(
                "işlem aktivitesi belirgin"
            )

        # Aktivite ivmeleniyor.
        if acceleration >= 0.05:

            score += 10

            reasons.append(
                "aktivite ivmeleniyor"
            )

        # Çok güçlü ivme.
        if acceleration >= 0.15:

            score += 5

            reasons.append(
                "aktivite güçlü ivmeleniyor"
            )

        # Fiyat henüz kaçmadan aktivite geliyor.
        if (
            0.0
            <= price_change
            <= 3.0
        ):

            score += 5

            reasons.append(
                "aktivite fiyat hareketinden önce geliyor"
            )

        return (
            min(score, 30),
            reasons,
    )
    # ========================================================
    # V29 FİNAL SKOR
    # ========================================================

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

        """
        V29 ağırlıklı skor sistemi.

        Erken dönüş hedefi nedeniyle:

            KIVRIM
            MOMENTUM
            HACİM
            AKTİVİTE

        klasik trendden daha fazla önem taşır.
        """

        # Her bölümün kendi maksimum puanını
        # 0-100 aralığına çeviriyoruz.

        trend_norm = (
            trend_score / 30
        ) * 100

        momentum_norm = (
            momentum_score / 30
        ) * 100

        volume_norm = (
            volume_score / 25
        ) * 100

        acceleration_norm = (
            acceleration_score / 20
        ) * 100

        # ----------------------------------------------------
        # Ağırlıklı skor
        # ----------------------------------------------------

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

        return int(
            round(
                _clamp(
                    weighted,
                    0,
                    100,
                )
            )
        )


    # ========================================================
    # KALİTE
    # ========================================================

    def _quality(
        self,
        score: int,
        curvature_type: str,
        early_score: int,
        volume_ratio: float,
    ) -> str:

        # ----------------------------------------------------
        # Çok güçlü
        # ----------------------------------------------------

        if (
            score >= STRONG_SCORE
            and curvature_type
            == "KIVRIM ÖNCÜ"
            and early_score >= 45
            and volume_ratio >= 1.15
        ):

            return "ÇOK GÜÇLÜ"

        # ----------------------------------------------------
        # Güçlü
        # ----------------------------------------------------

        if (
            score >= 70
            and curvature_type
            in (
                "KIVRIM ÖNCÜ",
                "KIVRIM GELİŞİYOR",
            )
        ):

            return "GÜÇLÜ"

        # ----------------------------------------------------
        # İyi
        # ----------------------------------------------------

        if score >= EARLY_SCORE:

            return "İYİ"

        # ----------------------------------------------------
        # Aday
        # ----------------------------------------------------

        if score >= MIN_SCORE:

            return "ADAY"

        return "ZAYIF"


    # ========================================================
    # SİNYAL KARARI
    # ========================================================

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
    ) -> tuple[
        str,
        str,
        bool,
        str,
    ]:

        """
        V29 son karar mekanizması.

        Buradaki amaç:

            Her yüksek skoru AL yapmamak.

        Ama aynı zamanda:

            Gerçek erken KIVRIM'i
            V28 teyidi gelmedi diye
            kaçırmamak.

        """

        # ====================================================
        # 1 — SKOR YETERSİZ
        # ====================================================

        if score < self.min_score:

            return (
                "BEKLE",
                "YOK",
                True,
                "skor eşik altında",
            )

        # ====================================================
        # 2 — KIVRIM YOK
        # ====================================================

        if curvature_type == "YOK":

            return (
                "BEKLE",
                "YOK",
                True,
                "kıvrım oluşmadı",
            )

        # ====================================================
        # 3 — AŞIRI RSI
        # ====================================================

        #
        # RSI zaten 70 üzerine çıkmışsa,
        # yeni hareketi kovalamak istemiyoruz.
        #

        if (
            rsi >= RSI_HIGH
            and early_score < 50
        ):

            return (
                "BEKLE",
                "YOK",
                True,
                "RSI aşırı yükselmiş",
            )

        # ====================================================
        # 4 — HACİM KONTROLÜ
        # ====================================================

        #
        # Hacim tamamen ölü ise
        # KIVRIM sinyali vermiyoruz.
        #

        if volume_ratio < MIN_VOLUME_RATIO:

            return (
                "BEKLE",
                "YOK",
                True,
                "hacim desteği yetersiz",
            )

        # ====================================================
        # 5 — ÇOK GÜÇLÜ KIVRIM ÖNCÜ
        # ====================================================

        #
        # Bu bölüm V29'un en önemli taraflarından biri.
        #
        # V28 henüz AL demese bile:
        #
        #   güçlü KIVRIM
        #   + erkenlik
        #   + hacim
        #
        # varsa erken AL verebilir.
        #

        if (
            score >= STRONG_SCORE
            and curvature_type
            == "KIVRIM ÖNCÜ"
            and early_score >= 45
            and volume_ratio >= 1.10
        ):

            return (
                "AL",
                "1. TEYİT",
                False,
                "",
            )

        # ====================================================
        # 6 — V28 + V29
        # ====================================================

        #
        # İki motor aynı yöne bakıyorsa
        # daha güçlü teyit.
        #

        if (
            score >= EARLY_SCORE
            and curvature_type
            in (
                "KIVRIM ÖNCÜ",
                "KIVRIM GELİŞİYOR",
            )
            and (
                "AL"
                in v28_signal
                or v28_score >= 60
            )
        ):

            return (
                "AL",
                "2. TEYİT",
                False,
                "",
            )

        # ====================================================
        # 7 — V29 BAĞIMSIZ ÖNCÜ
        # ====================================================

        #
        # V28 henüz dönüşü yakalamamış olabilir.
        #
        # Ancak:
        #
        #   KIVRIM ÖNCÜ
        #   yüksek erkenlik
        #   hacim ivmesi
        #
        # varsa V29 kendi başına aday olabilir.
        #

        if (
            score >= 72
            and curvature_type
            == "KIVRIM ÖNCÜ"
            and early_score >= 50
            and volume_acceleration >= 0.05
        ):

            return (
                "AL",
                "V29 ÖNCÜ",
                False,
                "",
            )

        # ====================================================
        # 8 — GELİŞEN KIVRIM
        # ====================================================

        #
        # Henüz AL değil.
        #
        # Ama takip edilmeye değer.
        #

        if (
            score >= self.min_score
            and curvature_type
            == "KIVRIM GELİŞİYOR"
        ):

            return (
                "BEKLE",
                "İZLE",
                False,
                "",
            )

        # ====================================================
        # 9 — GENEL BEKLE
        # ====================================================

        return (
            "BEKLE",
            "İZLE",
            True,
            "yeterli teyit yok",
        )


    # ========================================================
    # COOLDOWN KONTROLÜ
    # ========================================================

    def _cooldown_check(
        self,
        symbol: str,
    ) -> bool:

        """
        Aynı coin için kısa aralıklarla
        tekrar tekrar AL gönderilmesini engeller.
        """

        now = time.time()

        previous = self._last_signals.get(
            symbol
        )

        # İlk sinyal.
        if previous is None:

            return True

        # Süre dolduysa yeni sinyale izin ver.
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
    # ANA V29 HESAPLAMA
    # ========================================================

    def calculate(
        self,
        symbol: str,
        df: pd.DataFrame,
    ) -> V29Result:

        """
        V29'un ana giriş noktası.

        Veri
          ↓
        indikatörler
          ↓
        trend
          ↓
        momentum
          ↓
        hacim
          ↓
        KIVRIM
          ↓
        piyasa aktivitesi
          ↓
        V28 teyidi
          ↓
        final skor
          ↓
        AL / BEKLE
        """

        symbol = str(
            symbol
        ).upper().strip()

        result = V29Result(
            symbol=symbol
        )

        # ====================================================
        # VERİ KONTROLÜ
        # ====================================================

        if (
            df is None
            or df.empty
        ):

            result.rejected = True

            result.reject_reason = (
                "veri yok"
            )

            return result

        # En az 30 mum.
        if len(df) < 30:

            result.rejected = True

            result.reject_reason = (
                "yeterli mum verisi yok"
            )

            return result

        # ====================================================
        # DATAFRAME HAZIRLA
        # ====================================================

        work = self.prepare_dataframe(
            df
        )

        if work.empty:

            result.rejected = True

            result.reject_reason = (
                "dataframe hazırlanamadı"
            )

            return result

        # ====================================================
        # TEKNİK DEĞERLER
        # ====================================================

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

        result.volume_ratio = values[
            "volume_ratio"
        ]

        result.volume_acceleration = values[
            "volume_acceleration"
        ]

        # ====================================================
        # TREND
        # ====================================================

        (
            trend_score,
            trend_reasons,
        ) = self._trend_score(
            work,
            values,
        )

        result.trend_score = (
            trend_score
        )

        # ====================================================
        # MOMENTUM
        # ====================================================

        (
            momentum_score,
            momentum_reasons,
        ) = self._momentum_score(
            work,
            values,
        )

        result.momentum_score = (
            momentum_score
        )

        # ====================================================
        # HACİM
        # ====================================================

        (
            volume_score,
            volume_reasons,
        ) = self._volume_score(
            values
        )

        result.volume_score = (
            volume_score
        )

        # ====================================================
        # HACİM İVMESİ
        # ====================================================

        acceleration_score = 0

        if (
            values[
                "volume_acceleration"
            ]
            >= 0.05
        ):

            acceleration_score += 10

        if (
            values[
                "volume_acceleration"
            ]
            >= 0.15
        ):

            acceleration_score += 10

        result.acceleration_score = min(
            acceleration_score,
            20,
        )

        # ====================================================
        # KIVRIM
        # ====================================================

        (
            curvature_score,
            curvature_type,
            early_score,
            curvature_reasons,
        ) = self._curvature_score(
            work,
            values,
        )

        result.curvature_score = (
            curvature_score
        )

        result.curvature_type = (
            curvature_type
        )

        result.early_score = (
            early_score
        )

        # ====================================================
        # V28 TEYİDİ
        # ====================================================

        (
            v28_signal,
            v28_score,
            v28_reasons,
        ) = self._v28_confirmation(
            work
        )

        # ====================================================
        # PİYASA AKTİVİTESİ
        # ====================================================

        (
            market_activity_score,
            activity_reasons,
        ) = self._market_activity_score(
            values
        )

        # ====================================================
        # FİNAL SKOR
        # ====================================================

        final_score = (
            self._calculate_final_score(
                trend_score=trend_score,
                momentum_score=momentum_score,
                volume_score=volume_score,
                acceleration_score=acceleration_score,
                curvature_score=curvature_score,
                early_score=early_score,
                v28_score=v28_score,
                market_activity_score=market_activity_score,
            )
        )

        result.score = int(
            _clamp(
                final_score,
                0,
                100,
            )
        )

        # ====================================================
        # KALİTE
        # ====================================================

        result.quality = (
            self._quality(
                score=result.score,
                curvature_type=result.curvature_type,
                early_score=result.early_score,
                volume_ratio=result.volume_ratio,
            )
        )

        # ====================================================
        # NEDENLERİ TOPLA
        # ====================================================

        result.reasons = []

        result.reasons.extend(
            trend_reasons
        )

        result.reasons.extend(
            momentum_reasons
        )

        result.reasons.extend(
            volume_reasons
        )

        result.reasons.extend(
            curvature_reasons
        )

        result.reasons.extend(
            activity_reasons
        )

        result.reasons.extend(
            v28_reasons
        )

        # Aynı açıklamaları temizle.
        result.reasons = list(
            dict.fromkeys(
                result.reasons
            )
        )

        # ====================================================
        # KARAR
        # ====================================================

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

        result.confirmation = (
            confirmation
        )

        result.rejected = rejected

        result.reject_reason = (
            reject_reason
        )

        # ====================================================
        # COOLDOWN
        # ====================================================

        if result.signal == "AL":

            if not self._cooldown_check(
                symbol
            ):

                result.signal = (
                    "BEKLE"
                )

                result.confirmation = (
                    "COOLDOWN"
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

        """
        Diğer modüller için sade V29 API'si.
        """

        result = self.calculate(
            symbol,
            df,
        )

        return result.to_dict()


# ============================================================
# GLOBAL V29 ENGINE
# ============================================================

v29_engine = V29Engine()


# ============================================================
# DIŞARIDAN ÇAĞRILABİLECEK FONKSİYON
# ============================================================

def calculate_v29_signal(
    symbol: str,
    df: pd.DataFrame,
) -> Dict[str, Any]:

    """
    V29 sinyal hesaplama.

    Örnek:

        result = calculate_v29_signal(
            "PEOPLETRY",
            df,
        )
    """

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

    """
    Eski sistem calculate_signal()
    kullanıyorsa V29'a yönlendirir.

    Böylece diğer dosyalarda gereksiz
    değişiklik yapma ihtiyacını azaltır.
    """

    return calculate_v29_signal(
        symbol,
        df,
    )


# ============================================================
# DEBUG / TEST
# ============================================================

def debug_v29(
    symbol: str,
    df: pd.DataFrame,
) -> None:

    """
    Telefonda / Railway loglarında
    V29'un neden sinyal verdiğini görmek için.
    """

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
        f"🌀 Kıvrım        : "
        f"{result['curvature_type']}"
    )

    print(
        f"🎯 Kıvrım Skoru  : "
        f"{result['curvature_score']}/100"
    )

    print(
        f"⚡ Erkenlik       : "
        f"{result['early_score']}/100"
    )

    print(
        f"📈 RSI            : "
        f"{result['rsi']:.2f}"
    )

    print(
        f"📊 Hacim Oranı    : "
        f"{result['volume_ratio']:.2f}x"
    )

    print(
        f"🚀 Hacim İvmesi   : "
        f"{result['volume_acceleration'] * 100:.2f}%"
    )

    print(
        f"🟢 Sinyal         : "
        f"{result['signal']}"
    )

    print(
        f"✅ Teyit          : "
        f"{result['confirmation']}"
    )

    print(
        f"⭐ Kalite         : "
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
            "  • Henüz yeterli neden oluşmadı."
        )

    print("=" * 64)
    print()


# ============================================================
# V29 DOSYA SONU
# ============================================================
#
# ÖNEMLİ:
#
# Bu dosyanın görevi:
#
#       VERİ
#        ↓
#       TEKNİK ANALİZ
#        ↓
#       KIVRIM
#        ↓
#       HACİM
#        ↓
#       MOMENTUM
#        ↓
#       PİYASA AKTİVİTESİ
#        ↓
#       V28 TEYİDİ
#        ↓
#       V29 SKOR
#        ↓
#       AL / BEKLE
#
# Telegram gönderimi burada yapılmaz.
#
# Telegram işlemi scanner / bot tarafında kalır.
#
# ============================================================
