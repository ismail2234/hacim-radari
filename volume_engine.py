from __future__ import annotations

import pandas as pd


def _get_volume_column(df: pd.DataFrame) -> str:
    """
    TRY işlem çiftlerinde gerçek işlem hacmi olarak
    quote asset volume'u kullanır.

    TRY çiftlerinde quote asset = TRY'dir.
    """

    if "quote_volume" in df.columns:
        return "quote_volume"

    if "volume" in df.columns:
        return "volume"

    raise ValueError(
        "Hacim sütunu bulunamadı."
    )


def calculate_volume_metrics(
    df: pd.DataFrame,
) -> dict[str, float | bool]:
    """
    Son mumun TL hacim davranışını analiz eder.
    """

    if df.empty:
        raise ValueError(
            "Hacim analizi için veri boş."
        )

    required = {
        "open",
        "high",
        "low",
        "close",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Eksik sütunlar: "
            f"{', '.join(sorted(missing))}"
        )

    volume_column = _get_volume_column(df)

    current = df.iloc[-1]

    close = float(current["close"])
    open_price = float(current["open"])
    high = float(current["high"])
    low = float(current["low"])

    volume_try = float(
        current[volume_column]
    )

    lookback = min(20, len(df))

    average_volume_try = float(
        df[volume_column]
        .tail(lookback)
        .mean()
    )

    previous_volume_try = (
        float(df.iloc[-2][volume_column])
        if len(df) >= 2
        else volume_try
    )

    volume_ratio = (
        volume_try / average_volume_try
        if average_volume_try > 0
        else 0.0
    )

    volume_change_pct = 0.0

    if previous_volume_try > 0:
        volume_change_pct = (
            (
                volume_try
                - previous_volume_try
            )
            / previous_volume_try
            * 100
        )

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

    candle_range = high - low

    if candle_range > 0:
        body_ratio = (
            abs(close - open_price)
            / candle_range
        )
    else:
        body_ratio = 0.0

    bullish_candle = (
        close > open_price
    )

    volume_expansion = (
        volume_ratio >= 1.5
    )

    strong_volume_expansion = (
        volume_ratio >= 2.0
    )

    positive_price_with_volume = (
        volume_expansion
        and price_change_pct > 0
        and bullish_candle
    )

    return {
        "volume_try": volume_try,
        "average_volume_try": average_volume_try,
        "volume_ratio": volume_ratio,
        "volume_change_pct": volume_change_pct,
        "price_change_pct": price_change_pct,
        "body_ratio": body_ratio,
        "bullish_candle": bullish_candle,
        "volume_expansion": volume_expansion,
        "strong_volume_expansion": (
            strong_volume_expansion
        ),
        "positive_price_with_volume": (
            positive_price_with_volume
        ),
    }


def detect_volume_acceleration(
    df: pd.DataFrame,
) -> bool:
    """
    TL hacminin artış hızını kontrol eder.

    Son üç mum:
        V1 > V2 > V3

    ise hacim ivmeleniyor kabul edilir.
    """

    if len(df) < 5:
        return False

    volume_column = _get_volume_column(df)

    volumes = (
        df[volume_column]
        .tail(5)
        .astype(float)
    )

    v1 = float(volumes.iloc[-1])
    v2 = float(volumes.iloc[-2])
    v3 = float(volumes.iloc[-3])

    if (
        v1 <= 0
        or v2 <= 0
        or v3 <= 0
    ):
        return False

    return (
        v1 > v2 > v3
    )


def detect_early_volume_signal(
    df: pd.DataFrame,
) -> bool:
    """
    Erken hacim hareketi için temel filtre.

    Amaç:
    Fiyat henüz çok uzaklaşmadan TL hacminin
    devreye girdiği bölgeleri yakalamaktır.
    """

    if len(df) < 20:
        return False

    metrics = calculate_volume_metrics(
        df
    )

    acceleration = (
        detect_volume_acceleration(df)
    )

    price_change = float(
        metrics["price_change_pct"]
    )

    volume_ratio = float(
        metrics["volume_ratio"]
    )

    return (
        volume_ratio >= 1.5
        and acceleration
        and -1.0 <= price_change <= 4.0
        and bool(
            metrics["bullish_candle"]
        )
    )
