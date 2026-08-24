from __future__ import annotations

import pandas as pd


def calculate_ema(
    series: pd.Series,
    period: int,
) -> pd.Series:
    """Üssel hareketli ortalama (EMA)."""

    if period <= 0:
        raise ValueError("EMA period pozitif olmalı.")

    return series.ewm(
        span=period,
        adjust=False,
    ).mean()


def calculate_rsi(
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Klasik RSI hesaplar."""

    if period <= 0:
        raise ValueError("RSI period pozitif olmalı.")

    delta = close.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    average_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = average_gain / average_loss.replace(
        0,
        pd.NA,
    )

    rsi = 100 - (100 / (1 + rs))

    # Fiyat düşüşü olmayan durumlarda RSI'ın 100'e
    # yaklaşmasını sağlarız.
    rsi = rsi.fillna(100)

    return rsi.clip(
        lower=0,
        upper=100,
    )


def calculate_macd(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD, sinyal ve histogram değerlerini döndürür."""

    if fast_period <= 0:
        raise ValueError("MACD fast period pozitif olmalı.")

    if slow_period <= 0:
        raise ValueError("MACD slow period pozitif olmalı.")

    if signal_period <= 0:
        raise ValueError("MACD signal period pozitif olmalı.")

    if fast_period >= slow_period:
        raise ValueError(
            "MACD fast period, slow period'den küçük olmalı."
        )

    fast_ema = calculate_ema(
        close,
        fast_period,
    )

    slow_ema = calculate_ema(
        close,
        slow_period,
    )

    macd = fast_ema - slow_ema

    signal = calculate_ema(
        macd,
        signal_period,
    )

    histogram = macd - signal

    return macd, signal, histogram


def add_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    DataFrame'e EMA, RSI ve MACD göstergelerini ekler.

    Orijinal DataFrame'i değiştirmez.
    """

    if df.empty:
        raise ValueError(
            "İndikatör hesaplamak için DataFrame boş."
        )

    required_columns = {"close"}

    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"Eksik sütunlar: {', '.join(sorted(missing))}"
        )

    result = df.copy()

    result["ema_9"] = calculate_ema(
        result["close"],
        9,
    )

    result["ema_21"] = calculate_ema(
        result["close"],
        21,
    )

    result["ema_50"] = calculate_ema(
        result["close"],
        50,
    )

    result["rsi_14"] = calculate_rsi(
        result["close"],
        14,
    )

    (
        result["macd"],
        result["macd_signal"],
        result["macd_histogram"],
    ) = calculate_macd(
        result["close"]
    )

    return result
