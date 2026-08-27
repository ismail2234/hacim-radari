from __future__ import annotations

import pandas as pd


# ============================================================
# 🐋 BALİNA RADARI V30 — HACİM MOTORU
# ============================================================
#
# ANA FİKİR:
#
# Hacim bizim birinci önceliğimiz.
#
# Ama:
#     yüksek hacim = otomatik AL değildir.
#
# Aradığımız yapı:
#
#     hacim canlanıyor
#          ↓
#     hacim ivmeleniyor
#          ↓
#     fiyat henüz kaçmamış
#          ↓
#     fiyat hacme olumlu tepki veriyor
#          ↓
#     AL
#
# Büyük hacim patlaması + olumlu fiyat:
#
#     🔥 ÇOK GÜÇLÜ AL
#
# ============================================================


def _get_volume_column(df: pd.DataFrame) -> str:
    """
    TRY çiftlerinde gerçek işlem hacmi olarak
    quote_volume kullanılır.

    Binance TR:
        quote_volume = TL cinsinden işlem hacmi
    """

    if "quote_volume" in df.columns:
        return "quote_volume"

    if "volume" in df.columns:
        return "volume"

    raise ValueError(
        "Hacim sütunu bulunamadı."
    )


def _safe_float(
    value,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)

        if pd.isna(result):
            return default

        return result

    except (
        TypeError,
        ValueError,
    ):
        return default


# ============================================================
# TEMEL HACİM METRİKLERİ
# ============================================================

def calculate_volume_metrics(
    df: pd.DataFrame,
) -> dict[str, float | bool]:
    """
    TL hacmini analiz eder.

    Özellikle:

        • mevcut TL hacmi
        • kısa ortalama
        • hacim oranı
        • önceki mum değişimi
        • son birkaç mumdaki hacim eğilimi
        • fiyat-hacim ilişkisi
        • hacim patlaması

    hesaplanır.
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

    current = df.iloc[-1]

    close = _safe_float(
        current["close"]
    )

    open_price = _safe_float(
        current["open"]
    )

    high = _safe_float(
        current["high"]
    )

    low = _safe_float(
        current["low"]
    )

    current_volume = _safe_float(
        current[volume_column]
    )

    # --------------------------------------------------------
    # Ortalama hacimler
    # --------------------------------------------------------

    lookback_20 = min(
        20,
        len(df),
    )

    lookback_10 = min(
        10,
        len(df),
    )

    lookback_5 = min(
        5,
        len(df),
    )

    average_volume_20 = _safe_float(
        df[volume_column]
        .tail(lookback_20)
        .astype(float)
        .mean()
    )

    average_volume_10 = _safe_float(
        df[volume_column]
        .tail(lookback_10)
        .astype(float)
        .mean()
    )

    average_volume_5 = _safe_float(
        df[volume_column]
        .tail(lookback_5)
        .astype(float)
        .mean()
    )

    # --------------------------------------------------------
    # Önceki mum hacmi
    # --------------------------------------------------------

    previous_volume = (
        _safe_float(
            df.iloc[-2][
                volume_column
            ]
        )
        if len(df) >= 2
        else current_volume
    )

    # --------------------------------------------------------
    # Hacim oranı
    # --------------------------------------------------------

    volume_ratio = (
        current_volume
        / average_volume_20
        if average_volume_20 > 0
        else 0.0
    )

    volume_ratio_10 = (
        current_volume
        / average_volume_10
        if average_volume_10 > 0
        else 0.0
    )

    # --------------------------------------------------------
    # Hacim değişimi
    # --------------------------------------------------------

    volume_change_pct = 0.0

    if previous_volume > 0:
        volume_change_pct = (
            (
                current_volume
                - previous_volume
            )
            / previous_volume
            * 100
        )

    # --------------------------------------------------------
    # Fiyat değişimi
    # --------------------------------------------------------

    price_change_pct = 0.0

    if open_price > 0:
        price_change_pct = (
            (
                close
                - open_price
            )
            / open_price
            * 100
        )

    # --------------------------------------------------------
    # Mum yapısı
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
    # Son 3 mum hacmi
    # --------------------------------------------------------

    volume_trend_up = False

    if len(df) >= 3:

        v1 = _safe_float(
            df.iloc[-1][
                volume_column
            ]
        )

        v2 = _safe_float(
            df.iloc[-2][
                volume_column
            ]
        )

        v3 = _safe_float(
            df.iloc[-3][
                volume_column
            ]
        )

        volume_trend_up = (
            v1 > v2 > v3
        )

    # --------------------------------------------------------
    # Son 4 mum hacmi
    # --------------------------------------------------------

    volume_building = False

    if len(df) >= 4:

        v1 = _safe_float(
            df.iloc[-1][
                volume_column
            ]
        )

        v2 = _safe_float(
            df.iloc[-2][
                volume_column
            ]
        )

        v3 = _safe_float(
            df.iloc[-3][
                volume_column
            ]
        )

        v4 = _safe_float(
            df.iloc[-4][
                volume_column
            ]
        )

        # Tamamen monoton olmak zorunda değil.
        # Son hacim ortalamasının üzerinde ve
        # kısa ortalama uzun ortalamaya yaklaşıyor.
        short_average = (
            (v1 + v2)
            / 2
        )

        previous_average = (
            (v3 + v4)
            / 2
        )

        volume_building = (
            short_average
            > previous_average
            and v1 > v3
        )

    # --------------------------------------------------------
    # Hacim ivmesi
    # --------------------------------------------------------

    volume_acceleration = 0.0

    if previous_volume > 0:
        volume_acceleration = (
            current_volume
            / previous_volume
        ) - 1.0

    # --------------------------------------------------------
    # Hacim patlaması
    #
    # 20 mum ortalamasının en az 2 katı.
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
    # Hacim + pozitif fiyat
    # --------------------------------------------------------

    positive_price_with_volume = (
        current_volume > 0
        and bullish_candle
        and price_change_pct > 0
    )

    # --------------------------------------------------------
    # Hacim yükselirken fiyat henüz kaçmamış
    # --------------------------------------------------------

    early_volume_setup = (
        volume_building
        and volume_ratio >= 0.90
        and volume_ratio < 2.50
        and -1.5
        <= price_change_pct
        <= 3.0
    )

    # --------------------------------------------------------
    # Güçlü hacim + pozitif fiyat
    # --------------------------------------------------------

    strong_volume_price_move = (
        volume_ratio >= 1.50
        and bullish_candle
        and price_change_pct > 0
    )

    return {
        "volume_try": current_volume,

        "average_volume_try":
            average_volume_20,

        "average_volume_10":
            average_volume_10,

        "average_volume_5":
            average_volume_5,

        "volume_ratio":
            volume_ratio,

        "volume_ratio_10":
            volume_ratio_10,

        "volume_change_pct":
            volume_change_pct,

        "volume_acceleration":
            volume_acceleration,

        "price_change_pct":
            price_change_pct,

        "body_ratio":
            body_ratio,

        "bullish_candle":
            bullish_candle,

        "volume_trend_up":
            volume_trend_up,

        "volume_building":
            volume_building,

        "volume_expansion":
            volume_expansion,

        "strong_volume_expansion":
            strong_volume_expansion,

        "volume_spike":
            volume_spike,

        "positive_price_with_volume":
            positive_price_with_volume,

        "early_volume_setup":
            early_volume_setup,

        "strong_volume_price_move":
            strong_volume_price_move,
    }


# ============================================================
# HACİM İVMESİ
# ============================================================

def detect_volume_acceleration(
    df: pd.DataFrame,
) -> bool:
    """
    Son birkaç mumda TL hacminin
    yukarı doğru hareket edip etmediğini kontrol eder.

    Sadece:

        V1 > V2 > V3

    şartına bağlı değildir.

    Çünkü gerçek piyasada hacim:

        600K
        650K
        620K
        800K

    gibi dalgalanarak da hazırlanabilir.
    """

    if df is None or len(df) < 4:
        return False

    volume_column = (
        _get_volume_column(df)
    )

    volumes = (
        df[volume_column]
        .tail(4)
        .astype(float)
        .tolist()
    )

    if any(
        value <= 0
        for value in volumes
    ):
        return False

    v1 = volumes[-1]
    v2 = volumes[-2]
    v3 = volumes[-3]
    v4 = volumes[-4]

    recent_average = (
        v1 + v2
    ) / 2

    previous_average = (
        v3 + v4
    ) / 2

    return (
        recent_average
        > previous_average
        and v1 > v3
    )


# ============================================================
# ERKEN HACİM HAZIRLIĞI
# ============================================================

def detect_early_volume_signal(
    df: pd.DataFrame,
) -> bool:
    """
    Büyük hacim patlamasından önceki
    hazırlık bölgesini yakalamaya çalışır.

    Amaç:

        Hacim canlanıyor
        +
        fiyat henüz kaçmamış

    """

    if df is None or len(df) < 20:
        return False

    metrics = (
        calculate_volume_metrics(df)
    )

    acceleration = (
        detect_volume_acceleration(df)
    )

    volume_ratio = _safe_float(
        metrics["volume_ratio"]
    )

    price_change = _safe_float(
        metrics["price_change_pct"]
    )

    volume_building = bool(
        metrics["volume_building"]
    )

    bullish = bool(
        metrics["bullish_candle"]
    )

    # Fiyat çoktan kaçmışsa erken sinyal değil.
    price_not_escaped = (
        -1.5
        <= price_change
        <= 3.0
    )

    return (
        (
            volume_building
            or acceleration
        )
        and volume_ratio >= 0.90
        and price_not_escaped
        and bullish
    )


# ============================================================
# AL SEVİYESİ
# ============================================================

def get_volume_signal(
    df: pd.DataFrame,
) -> str:
    """
    Dışarıya sadece iki sinyal verir:

        AL
        ÇOK GÜÇLÜ AL

    Diğer durumlarda:

        YOK

    """

    if df is None or len(df) < 20:
        return "YOK"

    metrics = (
        calculate_volume_metrics(df)
    )

    volume_ratio = _safe_float(
        metrics["volume_ratio"]
    )

    volume_change = _safe_float(
        metrics["volume_change_pct"]
    )

    price_change = _safe_float(
        metrics["price_change_pct"]
    )

    bullish = bool(
        metrics["bullish_candle"]
    )

    volume_trend_up = bool(
        metrics["volume_trend_up"]
    )

    volume_building = bool(
        metrics["volume_building"]
    )

    # ========================================================
    # 🔥 ÇOK GÜÇLÜ AL
    # ========================================================
    #
    # Büyük hacim + pozitif fiyat.
    #

    if (
        volume_ratio >= 2.00
        and bullish
        and price_change > 0
    ):
        return "ÇOK GÜÇLÜ AL"

    # Çok büyük hacim patlaması.
    if (
        volume_ratio >= 3.00
        and price_change >= 0
    ):
        return "ÇOK GÜÇLÜ AL"

    # ========================================================
    # 🟢 AL
    # ========================================================
    #
    # Hacim henüz patlamamış olabilir.
    # Ancak yükselmeye / hazırlanmaya başlamış.
    #

    if (
        volume_building
        and volume_ratio >= 0.90
        and price_change > 0
        and price_change <= 3.0
    ):
        return "AL"

    if (
        volume_trend_up
        and volume_change >= 8.0
        and price_change > 0
        and price_change <= 3.0
    ):
        return "AL"

    return "YOK"
