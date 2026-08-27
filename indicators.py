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
    high = df["high"]
    low = df["low"]

    high_9 = high.rolling(9).max()
    low_9 = low.rolling(9).min()
    ichimoku_conversion = (high_9 + low_9) / 2.0

    high_26 = high.rolling(26).max()
    low_26 = low.rolling(26).min()
    ichimoku_base = (high_26 + low_26) / 2.0

    ichimoku_span_a = (
        ichimoku_conversion + ichimoku_base
    ) / 2.0

    high_52 = high.rolling(52).max()
    low_52 = low.rolling(52).min()
    ichimoku_span_b = (high_52 + low_52) / 2.0

    cols = {col: df[col] for col in df.columns}
    cols.update(
        {
            "ichimoku_conversion": ichimoku_conversion,
            "ichimoku_base": ichimoku_base,
            "ichimoku_span_a": ichimoku_span_a,
            "ichimoku_span_b": ichimoku_span_b,
        }
    )
    return pd.DataFrame(cols, index=df.index)


def add_structure(
    df: pd.DataFrame,
    lookback: int = 10,
) -> pd.DataFrame:

    close = df["close"]
    low = df["low"]
    high = df["high"]

    recent_low = low.rolling(lookback).min()
    recent_high = high.rolling(lookback).max()

    # Fiyatın dip bölgesine yakınlığı
    near_dip = close <= recent_low * 1.025

    # Higher-Low yapısı
    previous_low = recent_low.shift(lookback)
    higher_low = recent_low > previous_low

    # Kıvrımın ilk tespit koşulu
    ema_7 = df["ema_7"] if "ema_7" in df.columns else calculate_ema(close, 7)

    curve_up = (
        (close > close.shift(1))
        & (low >= low.shift(1))
        & (close > ema_7)
    )

    cols = {col: df[col] for col in df.columns}
    cols.update(
        {
            "recent_low": recent_low,
            "recent_high": recent_high,
            "near_dip": near_dip,
            "higher_low": higher_low,
            "curve_up": curve_up,
        }
    )
    return pd.DataFrame(cols, index=df.index)


def add_fibonacci(
    df: pd.DataFrame,
    lookback: int = 50,
) -> pd.DataFrame:

    high = df["high"]
    low = df["low"]
    close = df["close"]

    swing_high = high.rolling(lookback).max()
    swing_low = low.rolling(lookback).min()

    price_range = (swing_high - swing_low).replace(0, np.nan)

    fib_382 = swing_high - price_range * 0.382
    fib_500 = swing_high - price_range * 0.500
    fib_618 = swing_high - price_range * 0.618

    fib_zone = (close >= fib_618) & (close <= fib_382)

    cols = {col: df[col] for col in df.columns}
    cols.update(
        {
            "fib_382": fib_382,
            "fib_500": fib_500,
            "fib_618": fib_618,
            "fib_zone": fib_zone,
        }
    )
    return pd.DataFrame(cols, index=df.index)


def add_volume_profile(
    df: pd.DataFrame,
    bins: int = 20,
) -> pd.DataFrame:

    high = df["high"]
    low = df["low"]
    close = df["close"]
    vol = df["volume"]

    price_low = float(low.min())
    price_high = float(high.max())

    if price_high <= price_low:
        volume_profile_level = close
        volume_profile_support = pd.Series(False, index=df.index)
    else:
        edges = np.linspace(
            price_low,
            price_high,
            bins + 1,
        )

        # Optimization: Vectorized binning with np.digitize and aggregation with np.bincount
        # avoid expensive pandas pd.cut and groupby.sum
        typical_price = (
            high.to_numpy()
            + low.to_numpy()
            + close.to_numpy()
        ) / 3.0

        bins_idx = np.clip(
            np.digitize(typical_price, edges, right=True) - 1,
            0,
            bins - 1,
        )

        profile = np.bincount(
            bins_idx,
            weights=vol.to_numpy(),
            minlength=bins,
        )

        if profile.size == 0 or profile.sum() == 0:
            volume_profile_level = close
            volume_profile_support = pd.Series(False, index=df.index)
        else:
            poc_bucket = int(np.argmax(profile))
            poc_level = float(
                (edges[poc_bucket] + edges[poc_bucket + 1]) / 2.0
            )

            volume_profile_level = float(poc_level)
            volume_profile_support = (
                (close - poc_level).abs() / close <= 0.02
            )

    cols = {col: df[col] for col in df.columns}
    cols.update(
        {
            "volume_profile_level": volume_profile_level,
            "volume_profile_support": volume_profile_support,
        }
    )
    return pd.DataFrame(cols, index=df.index)


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

    close = df["close"]
    high = df["high"]
    low = df["low"]
    vol = df["volume"]

    # EMA
    ema_7 = calculate_ema(close, 7)
    ema_9 = calculate_ema(close, 9)
    ema_21 = calculate_ema(close, 21)
    ema_50 = calculate_ema(close, 50)

    # RSI
    rsi_14 = calculate_rsi(close, 14)

    # MACD
    macd, macd_signal, macd_histogram = calculate_macd(close)

    # Ichimoku
    high_9 = high.rolling(9).max()
    low_9 = low.rolling(9).min()
    ichimoku_conversion = (high_9 + low_9) / 2.0

    high_26 = high.rolling(26).max()
    low_26 = low.rolling(26).min()
    ichimoku_base = (high_26 + low_26) / 2.0

    ichimoku_span_a = (
        ichimoku_conversion + ichimoku_base
    ) / 2.0

    high_52 = high.rolling(52).max()
    low_52 = low.rolling(52).min()
    ichimoku_span_b = (high_52 + low_52) / 2.0

    # Structure
    lookback = 10
    recent_low = low.rolling(lookback).min()
    recent_high = high.rolling(lookback).max()
    near_dip = close <= recent_low * 1.025
    previous_low = recent_low.shift(lookback)
    higher_low = recent_low > previous_low
    curve_up = (
        (close > close.shift(1))
        & (low >= low.shift(1))
        & (close > ema_7)
    )

    # Fibonacci
    fib_lookback = 50
    swing_high = high.rolling(fib_lookback).max()
    swing_low = low.rolling(fib_lookback).min()
    price_range = (swing_high - swing_low).replace(0, np.nan)
    fib_382 = swing_high - price_range * 0.382
    fib_500 = swing_high - price_range * 0.500
    fib_618 = swing_high - price_range * 0.618
    fib_zone = (close >= fib_618) & (close <= fib_382)

    # Volume Profile
    price_low = float(low.min())
    price_high = float(high.max())
    bins = 20
    if price_high <= price_low:
        volume_profile_level = close
        volume_profile_support = pd.Series(False, index=df.index)
    else:
        edges = np.linspace(price_low, price_high, bins + 1)
        typical_price = (
            high.to_numpy()
            + low.to_numpy()
            + close.to_numpy()
        ) / 3.0

        bins_idx = np.clip(
            np.digitize(typical_price, edges, right=True) - 1,
            0,
            bins - 1,
        )

        profile = np.bincount(
            bins_idx,
            weights=vol.to_numpy(),
            minlength=bins,
        )

        if profile.size == 0 or profile.sum() == 0:
            volume_profile_level = close
            volume_profile_support = pd.Series(False, index=df.index)
        else:
            poc_bucket = int(np.argmax(profile))
            poc_level = float(
                (edges[poc_bucket] + edges[poc_bucket + 1]) / 2.0
            )

            volume_profile_level = float(poc_level)
            volume_profile_support = (
                (close - poc_level).abs() / close <= 0.02
            )

    # Optimized bulk DataFrame construction: avoids repeated df copies and re-indexing
    cols = {col: df[col] for col in df.columns}
    cols.update(
        {
            "ema_7": ema_7,
            "ema_9": ema_9,
            "ema_21": ema_21,
            "ema_50": ema_50,
            "rsi_14": rsi_14,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
            "ichimoku_conversion": ichimoku_conversion,
            "ichimoku_base": ichimoku_base,
            "ichimoku_span_a": ichimoku_span_a,
            "ichimoku_span_b": ichimoku_span_b,
            "recent_low": recent_low,
            "recent_high": recent_high,
            "near_dip": near_dip,
            "higher_low": higher_low,
            "curve_up": curve_up,
            "fib_382": fib_382,
            "fib_500": fib_500,
            "fib_618": fib_618,
            "fib_zone": fib_zone,
            "volume_profile_level": volume_profile_level,
            "volume_profile_support": volume_profile_support,
        }
    )

    return pd.DataFrame(cols, index=df.index)
