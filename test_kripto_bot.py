from unittest.mock import patch
import pandas as pd
import numpy as np
import pytest
import kripto_bot

def test_evaluate_insufficient_data():
    df = pd.DataFrame({
        "timestamp": range(10),
        "open": range(10),
        "high": range(10),
        "low": range(10),
        "close": range(10),
        "volume": range(10)
    })
    with patch("kripto_bot.get_ohlcv", return_value=df):
        res = kripto_bot.evaluate("BTC/TRY")
        assert res is None

def test_evaluate_valid_signal():
    min_needed = max(kripto_bot.LONG_WINDOW, kripto_bot.RSI_PERIOD) + 20
    np.random.seed(10)
    prices = 100.0 + np.cumsum(np.random.normal(0, 0.5, min_needed))
    volumes = np.full(min_needed, 1000.0)
    volumes[-1] = 2000.0  # volume spike

    df = pd.DataFrame({
        "timestamp": range(min_needed),
        "open": prices,
        "high": prices + 1,
        "low": prices - 1,
        "close": prices,
        "volume": volumes
    })

    with patch("kripto_bot.get_ohlcv", return_value=df):
        res = kripto_bot.evaluate("BTC/TRY")
        assert res is not None
        assert "price" in res
        assert "score" in res
        assert res["score"] >= kripto_bot.MIN_SCORE
        assert res["vol_ratio"] == pytest.approx(2000.0 / np.mean(volumes[-20:]))

def test_evaluate_low_score_returns_none():
    min_needed = max(kripto_bot.LONG_WINDOW, kripto_bot.RSI_PERIOD) + 20
    # Downward trending prices -> low score
    prices = np.linspace(150, 100, min_needed)
    volumes = np.full(min_needed, 1000.0)

    df = pd.DataFrame({
        "timestamp": range(min_needed),
        "open": prices,
        "high": prices + 1,
        "low": prices - 1,
        "close": prices,
        "volume": volumes
    })

    with patch("kripto_bot.get_ohlcv", return_value=df):
        res = kripto_bot.evaluate("BTC/TRY")
        assert res is None
