import pytest
from indicators import macd, ema


def test_macd_insufficient_data():
    closes = [10.0] * 30  # less than slow (26) + signal (9) = 35
    macd_line, signal_line, hist = macd(closes)
    assert macd_line == 0.0
    assert signal_line == 0.0
    assert hist == 0.0


def test_macd_constant_prices():
    closes = [100.0] * 50
    macd_line, signal_line, hist = macd(closes)
    assert pytest.approx(macd_line, abs=1e-9) == 0.0
    assert pytest.approx(signal_line, abs=1e-9) == 0.0
    assert pytest.approx(hist, abs=1e-9) == 0.0


def test_macd_trending_prices():
    closes = [float(i) for i in range(1, 51)]
    macd_line, signal_line, hist = macd(closes)

    # For strictly increasing prices, fast EMA > slow EMA, so MACD line > 0
    assert macd_line > 0.0
    assert pytest.approx(hist, abs=1e-9) == macd_line - signal_line
