from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators import (
    add_fibonacci,
    add_ichimoku,
    add_indicators,
    add_structure,
    add_volume_profile,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
)


def test_add_volume_profile_basic():
    df = pd.DataFrame({
        "open": [10.0] * 5,
        "high": [12.0, 15.0, 14.0, 13.0, 11.0],
        "low": [9.0, 10.0, 11.0, 8.0, 9.0],
        "close": [11.0, 14.0, 12.0, 10.0, 10.5],
        "volume": [100.0, 500.0, 200.0, 150.0, 120.0],
    })

    result = add_volume_profile(df, bins=10)

    assert "volume_profile_level" in result.columns
    assert "volume_profile_support" in result.columns
    assert len(result) == 5
    assert isinstance(result["volume_profile_level"].iloc[0], float)
    assert isinstance(bool(result["volume_profile_support"].iloc[0]), bool)


def test_add_volume_profile_flat_price():
    df = pd.DataFrame({
        "open": [10.0] * 5,
        "high": [10.0] * 5,
        "low": [10.0] * 5,
        "close": [10.0] * 5,
        "volume": [100.0] * 5,
    })

    result = add_volume_profile(df)

    assert (result["volume_profile_level"] == result["close"]).all()
    assert not result["volume_profile_support"].any()


def test_add_volume_profile_zero_volume():
    df = pd.DataFrame({
        "open": [10.0] * 5,
        "high": [12.0, 13.0, 14.0, 15.0, 16.0],
        "low": [8.0, 9.0, 10.0, 11.0, 12.0],
        "close": [10.0, 11.0, 12.0, 13.0, 14.0],
        "volume": [0.0] * 5,
    })

    result = add_volume_profile(df)

    assert (result["volume_profile_level"] == result["close"]).all()
    assert not result["volume_profile_support"].any()


def test_add_indicators_full():
    np.random.seed(42)
    n = 100
    close = 100 + np.cumsum(np.random.randn(n))
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.random.rand(n),
        "low": close - np.random.rand(n),
        "close": close,
        "volume": np.random.rand(n) * 1000 + 100,
    })

    result = add_indicators(df)

    required_cols = [
        "ema_7", "ema_9", "ema_21", "ema_50",
        "rsi_14", "macd", "macd_signal", "macd_histogram",
        "ichimoku_conversion", "ichimoku_base", "ichimoku_span_a", "ichimoku_span_b",
        "recent_low", "recent_high", "near_dip", "higher_low", "curve_up",
        "fib_382", "fib_500", "fib_618", "fib_zone",
        "volume_profile_level", "volume_profile_support",
    ]

    for col in required_cols:
        assert col in result.columns
