from unittest import mock
import numpy as np
import pytest
import kripto_bot


def generate_mock_ohlcv_signal():
    # 50 candles
    # To pass:
    # 1. trend_ok: prev_short <= prev_long and curr_short > curr_long
    # 2. momentum_ok: 50 <= curr_rsi <= 75 and curr_rsi > prev_rsi
    # 3. volume_ok: curr_vol / avg_vol >= 1.3
    closes = [100.0] * 30 + [80.0] * 19 + [88.0]
    for i in range(35, 49):
        if i % 2 == 0:
            closes[i] = closes[i-1] - 1.0
        else:
            closes[i] = closes[i-1] + 1.2
    closes[49] = closes[48] + 5.0

    volumes = [100.0] * 49 + [200.0]
    base_ts = 1700000000000

    data = []
    for i in range(50):
        c = closes[i]
        v = volumes[i]
        data.append([base_ts + i * 900000, c, c + 1.0, c - 1.0, c, v])

    return data


def test_evaluate_insufficient_data():
    with mock.patch("kripto_bot.get_ohlcv", return_value=[[0, 10, 11, 9, 10, 100]] * 10):
        result = kripto_bot.evaluate("BTC/TRY")
        assert result is None


def test_evaluate_empty_data():
    with mock.patch("kripto_bot.get_ohlcv", return_value=[]):
        result = kripto_bot.evaluate("BTC/TRY")
        assert result is None


def test_evaluate_valid_signal():
    data = generate_mock_ohlcv_signal()
    with mock.patch("kripto_bot.get_ohlcv", return_value=data):
        res = kripto_bot.evaluate("BTC/TRY")
        assert res is not None
        assert res["score"] >= kripto_bot.MIN_SCORE
        assert res["price"] == data[-1][4]
        assert "details" in res
        assert isinstance(res["rsi"], float)


def test_evaluate_zero_volume_avg():
    data = [[1700000000000 + i*900000, 100, 105, 95, 100, 0.0] for i in range(50)]
    with mock.patch("kripto_bot.get_ohlcv", return_value=data):
        res = kripto_bot.evaluate("BTC/TRY")
        assert res is None
