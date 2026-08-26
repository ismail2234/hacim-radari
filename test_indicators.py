import pytest
import numpy as np
import pandas as pd

from indicators import (
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    add_ichimoku,
    add_structure,
    add_fibonacci,
    add_volume_profile,
    add_indicators,
)


def sample_df(n: int = 100) -> pd.DataFrame:
    np.random.seed(42)
    close = 100.0 + np.cumsum(np.random.randn(n))
    high = close + np.abs(np.random.randn(n)) + 0.5
    low = close - np.abs(np.random.randn(n)) - 0.5
    open_price = close + np.random.randn(n) * 0.2
    volume = 1000.0 + np.abs(np.random.randn(n)) * 500

    return pd.DataFrame({
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def test_calculate_ema():
    series = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    ema = calculate_ema(series, 3)
    assert len(ema) == len(series)
    assert ema.iloc[-1] > ema.iloc[0]

    with pytest.raises(ValueError):
        calculate_ema(series, 0)


def test_calculate_rsi():
    series = pd.Series(range(1, 30), dtype=float)
    rsi = calculate_rsi(series, 14)
    assert len(rsi) == len(series)
    assert rsi.iloc[-1] == 100.0  # Monotonically increasing prices -> RSI 100

    with pytest.raises(ValueError):
        calculate_rsi(series, -1)


def test_calculate_macd():
    series = pd.Series(range(1, 100), dtype=float)
    macd, signal, hist = calculate_macd(series)
    assert len(macd) == len(series)
    assert len(signal) == len(series)
    assert len(hist) == len(series)


def test_add_ichimoku():
    df = sample_df()
    res = add_ichimoku(df)
    expected_cols = [
        "ichimoku_conversion",
        "ichimoku_base",
        "ichimoku_span_a",
        "ichimoku_span_b",
    ]
    for col in expected_cols:
        assert col in res.columns


def test_add_structure():
    df = sample_df()
    df["ema_7"] = calculate_ema(df["close"], 7)
    res = add_structure(df)
    expected_cols = ["recent_low", "recent_high", "near_dip", "higher_low", "curve_up"]
    for col in expected_cols:
        assert col in res.columns


def test_add_fibonacci():
    df = sample_df()
    res = add_fibonacci(df)
    expected_cols = ["fib_382", "fib_500", "fib_618", "fib_zone"]
    for col in expected_cols:
        assert col in res.columns


def test_add_volume_profile():
    df = sample_df()
    res = add_volume_profile(df, bins=20)
    assert "volume_profile_level" in res.columns
    assert "volume_profile_support" in res.columns


def test_add_volume_profile_flat():
    df = pd.DataFrame({
        "high": [10.0] * 10,
        "low": [10.0] * 10,
        "close": [10.0] * 10,
        "volume": [100.0] * 10,
    })
    res = add_volume_profile(df)
    assert (res["volume_profile_level"] == 10.0).all()
    assert not res["volume_profile_support"].any()


def test_add_indicators():
    df = sample_df(100)
    res = add_indicators(df)

    expected_cols = {
        "ema_7", "ema_9", "ema_21", "ema_50",
        "rsi_14", "macd", "macd_signal", "macd_histogram",
        "ichimoku_conversion", "ichimoku_base", "ichimoku_span_a", "ichimoku_span_b",
        "recent_low", "recent_high", "near_dip", "higher_low", "curve_up",
        "fib_382", "fib_500", "fib_618", "fib_zone",
        "volume_profile_level", "volume_profile_support"
    }

    for col in expected_cols:
        assert col in res.columns, f"Missing column {col}"

    assert len(res) == len(df)


def test_add_indicators_validation():
    with pytest.raises(ValueError, match="boş"):
        add_indicators(pd.DataFrame())

    with pytest.raises(ValueError, match="Eksik"):
        add_indicators(pd.DataFrame({"open": [1], "high": [2]}))
