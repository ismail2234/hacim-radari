from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    if period <= 0:
        raise ValueError("EMA period pozitif olmalı.")
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    if period <= 0:
        raise ValueError("RSI period pozitif olmalı.")

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(100).clip(0, 100)


def calculate_macd(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:

    fast = calculate_ema(close, fast_period)
    slow = calculate_ema(close, slow_period)

    macd = fast - slow
    signal = calculate_ema(macd, signal_period)
    histogram = macd - signal

    return macd, signal, histogram


def add_ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()

    high_9 = result["high"].rolling(9).max()
    low_9 = result["low"].rolling(9).min()

    result["ichimoku_conversion"] = (high_9 + low_9) / 2

    high_26 = result["high"].rolling(26).max()
    low_26 = result["low"].rolling(26).min()

    result["ichimoku_base"] = (high_26 + low_26) / 2

    result["ichimoku_span_a"] = (
        result["ichimoku_conversion"]
        + result["ichimoku_base"]
    ) / 2

    high_52 = result["high"].rolling(52).max()
    low_52 = result["low"].rolling(52).min()

    result["ichimoku_span_b"] = (
        high_52 + low_52
    ) / 2

    return result


def add_structure(
    df: pd.DataFrame,
    lookback: int = 10,
) -> pd.DataFrame:

    result = df.copy()

    result["recent_low"] = (
        result["low"].rolling(lookback).min()
    )

    result["recent_high"] = (
        result["high"].rolling(lookback).max()
    )

    # Fiyatın dip bölgesine yakınlığı
    result["near_dip"] = (
        result["close"]
        <= result["recent_low"] * 1.025
    )

    # Higher-Low yapısı
    previous_low = result["recent_low"].shift(lookback)

    result["higher_low"] = (
        result["recent_low"] > previous_low
    )

    # Kıvrımın ilk tespit koşulu
    result["curve_up"] = (
        (result["close"] > result["close"].shift(1))
        & (result["low"] >= result["low"].shift(1))
        & (result["close"] > result["ema_7"])
    )

    return result


def add_fibonacci(
    df: pd.DataFrame,
    lookback: int = 50,
) -> pd.DataFrame:

    result = df.copy()

    swing_high = (
        result["high"].rolling(lookback).max()
    )

    swing_low = (
        result["low"].rolling(lookback).min()
    )

    price_range = (
        swing_high - swing_low
    ).replace(0, np.nan)

    result["fib_382"] = (
        swing_high - price_range * 0.382
    )

    result["fib_500"] = (
        swing_high - price_range * 0.500
    )

    result["fib_618"] = (
        swing_high - price_range * 0.618
    )

    result["fib_zone"] = (
        (result["close"] >= result["fib_618"])
        & (result["close"] <= result["fib_382"])
    )

    return result


def add_volume_profile(
    df: pd.DataFrame,
    bins: int = 20,
) -> pd.DataFrame:

    result = df.copy()

    price_low = float(result["low"].min())
    price_high = float(result["high"].max())

    if price_high <= price_low:
        result["volume_profile_level"] = result["close"]
        result["volume_profile_support"] = False
        return result

    edges = np.linspace(
        price_low,
        price_high,
        bins + 1,
    )

    typical_price = (
        result["high"]
        + result["low"]
        + result["close"]
    ) / 3

    bucket = pd.cut(
        typical_price,
        bins=edges,
        labels=False,
        include_lowest=True,
    )

    profile = (
        result.assign(_bucket=bucket)
        .groupby("_bucket", observed=False)["volume"]
        .sum()
    )

    if profile.empty:
        result["volume_profile_level"] = result["close"]
        result["volume_profile_support"] = False
        return result

    poc_bucket = int(profile.idxmax())

    poc_level = (
        edges[poc_bucket]
        + edges[poc_bucket + 1]
    ) / 2

    result["volume_profile_level"] = float(
        poc_level
    )

    result["volume_profile_support"] = (
        (
            result["close"]
            - poc_level
        ).abs()
        / result["close"]
        <= 0.02
    )

    return result


def add_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:

    if df.empty:
        raise ValueError(
            "İndikatör hesaplamak için DataFrame boş."
        )

    required = {
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            "Eksik sütunlar: "
            + ", ".join(sorted(missing))
        )

    result = df.copy()

    # EMA
    result["ema_7"] = calculate_ema(
        result["close"], 7
    )

    result["ema_9"] = calculate_ema(
        result["close"], 9
    )

    result["ema_21"] = calculate_ema(
        result["close"], 21
    )

    result["ema_50"] = calculate_ema(
        result["close"], 50
    )

    # RSI
    result["rsi_14"] = calculate_rsi(
        result["close"], 14
    )

    # MACD
    (
        result["macd"],
        result["macd_signal"],
        result["macd_histogram"],
    ) = calculate_macd(
        result["close"]
    )

    # V28 yapıları
    result = add_ichimoku(result)
    result = add_structure(result)
    result = add_fibonacci(result)
    result = add_volume_profile(result)

    return result
