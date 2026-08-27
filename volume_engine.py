from __future__ import annotations

from typing import Any

import pandas as pd


# ============================================================
# 🐋 BALİNA RADARI — TL HACİM MOTORU
# ============================================================
#
# ANA MANTIK:
#
# quote_volume = TL işlem hacmi
#
# Hacim bizim ana tetikleyicimizdir.
#
# AL:
#   Hacim hazırlanmaya / yükselmeye başlıyor
#   + fiyat henüz fazla kaçmamış
#
# ÇOK GÜÇLÜ AL:
#   Hacim patlaması
#   + fiyat yukarı tepki veriyor
#
# Diğer her şey:
#   YOK
#
# ÖNEMLİ:
# Son mum, kendi hacim ortalamasına dahil edilmez.
# Böylece ani hacim patlaması bastırılmaz.
#
# ============================================================


# ============================================================
# YARDIMCI
# ============================================================

def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:

    try:
        result = float(value)

        if pd.isna(result):
            return default

        return result

    except (TypeError, ValueError):
        return default


# ============================================================
# TL HACİM KOLONU
# ============================================================

def _get_volume_column(
    df: pd.DataFrame,
) -> str:

    # Binance verisindeki quote_volume
    # bizim için TL işlem hacmidir.

    if "quote_volume" not in df.columns:

        raise ValueError(
            "TL hacim verisi (quote_volume) bulunamadı."
        )

    return "quote_volume"


# ============================================================
# TEMEL HACİM METRİKLERİ
# ============================================================

def calculate_volume_metrics(
    df: pd.DataFrame,
) -> dict[str, Any]:

    """
    Binance TR mumlarından TL hacim davranışını hesaplar.
    """

    if df is None or df.empty:

        raise ValueError(
            "Hacim analizi için veri boş."
        )

    required = {
        "open",
        "high",
        "low",
        "close",
        "quote_volume",
    }

    missing = (
        required
        - set(df.columns)
    )

    if missing:

        raise ValueError(
            "Eksik sütunlar: "
            + ", ".join(
                sorted(missing)
            )
        )

    volume_column = (
        _get_volume_column(df)
    )

    work = df.copy()

    # --------------------------------------------------------
    # Sayısallaştır
    # --------------------------------------------------------

    work[volume_column] = pd.to_numeric(
        work[volume_column],
        errors="coerce",
    )

    work["open"] = pd.to_numeric(
        work["open"],
        errors="coerce",
    )

    work["high"] = pd.to_numeric(
        work["high"],
        errors="coerce",
    )

    work["low"] = pd.to_numeric(
        work["low"],
        errors="coerce",
    )

    work["close"] = pd.to_numeric(
        work["close"],
        errors="coerce",
    )

    work = work.dropna(
        subset=[
            volume_column,
            "open",
            "high",
            "low",
            "close",
        ]
    )

    if len(work) < 2:

        raise ValueError(
            "Hacim analizi için yeterli veri yok."
        )

    # --------------------------------------------------------
    # SON MUM
    # --------------------------------------------------------

    current = work.iloc[-1]

    current_volume = _safe_float(
        current[volume_column]
    )

    open_price = _safe_float(
        current["open"]
    )

    close = _safe_float(
        current["close"]
    )

    high = _safe_float(
        current["high"]
    )

    low = _safe_float(
        current["low"]
    )

    # --------------------------------------------------------
    # ÖNCEKİ MUM
    # --------------------------------------------------------

    previous_volume = _safe_float(
        work.iloc[-2][volume_column],
        current_volume,
    )

    previous_close = _safe_float(
        work.iloc[-2]["close"],
        close,
    )

    # --------------------------------------------------------
    # HACİM REFERANSI
    # --------------------------------------------------------
    #
    # ÇOK ÖNEMLİ:
    #
    # Son mum ortalamaya dahil edilmiyor.
    #
    # Böylece:
    #
    # normal hacim
    #      ↓
    # yükselen hacim
    #      ↓
    # patlama
    #
    # davranışını daha net yakalayabiliriz.
    #

    previous_volumes = (
        work[volume_column]
        .iloc[:-1]
    )

    lookback = min(
        20,
        len(previous_volumes),
    )

    baseline = _safe_float(
        previous_volumes
        .tail(lookback)
        .mean()
    )

    if baseline <= 0:

        volume_ratio = 0.0

    else:

        volume_ratio = (
            current_volume
            / baseline
        )

    # --------------------------------------------------------
    # ÖNCEKİ HACİM ORANI
    # --------------------------------------------------------

    previous_history = (
        work[volume_column]
        .iloc[:-2]
    )

    previous_lookback = min(
        20,
        len(previous_history),
    )

    if (
        previous_history.empty
        or previous_lookback <= 0
    ):

        previous_baseline = baseline

    else:

        previous_baseline = _safe_float(
            previous_history
            .tail(previous_lookback)
            .mean()
        )

    if previous_baseline > 0:

        previous_ratio = (
            previous_volume
            / previous_baseline
        )

    else:

        previous_ratio = 0.0

    # --------------------------------------------------------
    # HACİM DEĞİŞİMİ
    # --------------------------------------------------------

    if previous_volume > 0:

        volume_change_pct = (
            (
                current_volume
                - previous_volume
            )
            / previous_volume
        ) * 100.0

        volume_acceleration = (
            current_volume
            / previous_volume
        ) - 1.0

    else:

        volume_change_pct = 0.0
        volume_acceleration = 0.0

    # --------------------------------------------------------
    # FİYAT DEĞİŞİMİ
    # --------------------------------------------------------

    if open_price > 0:

        price_change_pct = (
            (
                close
                - open_price
            )
            / open_price
        ) * 100.0

    else:

        price_change_pct = 0.0

    # --------------------------------------------------------
    # ÖNCEKİ KAPANIŞA GÖRE FİYAT
    # --------------------------------------------------------

    if previous_close > 0:

        price_change_from_previous_pct = (
            (
                close
                - previous_close
            )
            / previous_close
        ) * 100.0

    else:

        price_change_from_previous_pct = 0.0

    # --------------------------------------------------------
    # MUM YAPISI
    # --------------------------------------------------------

    candle_range = (
        high - low
    )

    if candle_range > 0:

        body_ratio = (
            abs(
                close
                - open_price
            )
            / candle_range
        )

    else:

        body_ratio = 0.0

    bullish_candle = (
        close > open_price
    )

    # --------------------------------------------------------
    # SON 3-4 MUM HACİM DAVRANIŞI
    # --------------------------------------------------------

    recent_volumes = (
        work[volume_column]
        .tail(4)
        .tolist()
    )

    volume_trend_up = False

    if len(recent_volumes) >= 3:

        volume_trend_up = (
            recent_volumes[-1]
            > recent_volumes[-2]
            > recent_volumes[-3]
        )

    # --------------------------------------------------------
    # HACİM HAZIRLANIYOR MU?
    # --------------------------------------------------------

    volume_building = False

    if len(recent_volumes) >= 4:

        recent_average = (
            recent_volumes[-1]
            + recent_volumes[-2]
        ) / 2.0

        previous_average = (
            recent_volumes[-3]
            + recent_volumes[-4]
        ) / 2.0

        volume_building = (
            recent_average
            > previous_average
            and recent_volumes[-1]
            > recent_volumes[-3]
        )

    # --------------------------------------------------------
    # HACİM SEVİYELERİ
    # --------------------------------------------------------

    volume_expansion = (
        volume_ratio >= 1.50
    )

    strong_volume_expansion = (
        volume_ratio >= 2.00
    )

    volume_spike = (
        volume_ratio >= 3.00
    )

    # --------------------------------------------------------
    # HACİM + FİYAT
    # --------------------------------------------------------

    positive_price_with_volume = (
        current_volume > 0
        and bullish_candle
        and price_change_pct > 0
    )

    # --------------------------------------------------------
    # ERKEN HACİM YAPISI
    # --------------------------------------------------------
    #
    # Burada henüz devasa patlama istemiyoruz.
    #
    # Ama hacim hazırlanıyor olmalı.
    #

    early_volume_setup = (
        (
            volume_building
            or volume_acceleration >= 0.05
        )
        and volume_ratio >= 0.90
        and volume_ratio < 2.50
        and -1.5 <= price_change_pct <= 3.0
        and bullish_candle
    )

    # --------------------------------------------------------
    # GÜÇLÜ HACİM + FİYAT
    # --------------------------------------------------------

    strong_volume_price_move = (
        volume_ratio >= 1.50
        and bullish_candle
        and price_change_pct > 0
    )

    return {

        # ----------------------------------------------------
        # TL HACİM
        # ----------------------------------------------------

        "volume_try": current_volume,

        "average_volume_try": baseline,

        "previous_volume_try": previous_volume,

        # ----------------------------------------------------
        # ORANLAR
        # ----------------------------------------------------

        "volume_ratio": volume_ratio,

        "previous_volume_ratio": previous_ratio,

        # ----------------------------------------------------
        # HACİM DEĞİŞİMİ
        # ----------------------------------------------------

        "volume_change_pct": (
            volume_change_pct
        ),

        "volume_acceleration": (
            volume_acceleration
        ),

        # ----------------------------------------------------
        # FİYAT
        # ----------------------------------------------------

        "price_change_pct": (
            price_change_pct
        ),

        "price_change_from_previous_pct": (
            price_change_from_previous_pct
        ),

        # ----------------------------------------------------
        # MUM
        # ----------------------------------------------------

        "body_ratio": body_ratio,

        "bullish_candle": (
            bullish_candle
        ),

        # ----------------------------------------------------
        # HACİM DAVRANIŞI
        # ----------------------------------------------------

        "volume_trend_up": (
            volume_trend_up
        ),

        "volume_building": (
            volume_building
        ),

        "volume_expansion": (
            volume_expansion
        ),

        "strong_volume_expansion": (
            strong_volume_expansion
        ),

        "volume_spike": (
            volume_spike
        ),

        # ----------------------------------------------------
        # BİRLEŞİK YAPILAR
        # ----------------------------------------------------

        "positive_price_with_volume": (
            positive_price_with_volume
        ),

        "early_volume_setup": (
            early_volume_setup
        ),

        "strong_volume_price_move": (
            strong_volume_price_move
        ),
    }


# ============================================================
# HACİM İVMESİ
# ============================================================

def detect_volume_acceleration(
    df: pd.DataFrame,
) -> bool:

    """
    TL hacminin son mumda hızlandığını kontrol eder.
    """

    if (
        df is None
        or len(df) < 4
    ):

        return False

    try:

        metrics = (
            calculate_volume_metrics(df)
        )

        return bool(
            metrics[
                "volume_building"
            ]
            or metrics[
                "volume_acceleration"
            ] >= 0.05
        )

    except (
        ValueError,
        TypeError,
        KeyError,
    ):

        return False


# ============================================================
# ERKEN HACİM SİNYALİ
# ============================================================

def detect_early_volume_signal(
    df: pd.DataFrame,
) -> bool:

    """
    Büyük hacim patlamasından önce
    hacmin hazırlanmasını yakalar.
    """

    if (
        df is None
        or len(df) < 20
    ):

        return False

    try:

        metrics = (
            calculate_volume_metrics(df)
        )

    except (
        ValueError,
        TypeError,
        KeyError,
    ):

        return False

    return bool(
        metrics[
            "early_volume_setup"
        ]
    )


# ============================================================
# HACİM SİNYALİ
# ============================================================

def get_volume_signal(
    df: pd.DataFrame,
) -> str:

    """
    Dışarıya yalnızca:

        AL
        ÇOK GÜÇLÜ AL
        YOK

    döndürür.
    """

    if (
        df is None
        or len(df) < 20
    ):

        return "YOK"

    try:

        metrics = (
            calculate_volume_metrics(df)
        )

    except (
        ValueError,
        TypeError,
        KeyError,
    ):

        return "YOK"

    ratio = _safe_float(
        metrics["volume_ratio"]
    )

    price_change = _safe_float(
        metrics["price_change_pct"]
    )

    bullish = bool(
        metrics["bullish_candle"]
    )

    building = bool(
        metrics["volume_building"]
    )

    trend_up = bool(
        metrics["volume_trend_up"]
    )

    volume_change = _safe_float(
        metrics["volume_change_pct"]
    )

    # ========================================================
    # 🔥 ÇOK GÜÇLÜ AL
    # ========================================================
    #
    # TL hacmi normalin en az 2 katı
    # ve mum yukarı yönlü.
    #

    if (
        ratio >= 2.00
        and bullish
        and price_change > 0
    ):

        return "ÇOK GÜÇLÜ AL"

    # Çok büyük hacim patlaması.
    if (
        ratio >= 3.00
        and price_change >= 0
    ):

        return "ÇOK GÜÇLÜ AL"

    # ========================================================
    # 🟢 AL
    # ========================================================
    #
    # Hacim hazırlanıyor.
    # Fiyat henüz fazla kaçmamış.
    #

    if (
        building
        and ratio >= 0.90
        and 0 < price_change <= 3.0
    ):

        return "AL"

    # --------------------------------------------------------
    # Üç mumluk hacim yükselişi
    # --------------------------------------------------------

    if (
        trend_up
        and volume_change >= 8.0
        and 0 < price_change <= 3.0
    ):

        return "AL"

    return "YOK"
