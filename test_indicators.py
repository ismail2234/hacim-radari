import math
import random
from indicators import ema, macd, rsi, sma, _ema_series


def test_ema_series_matches_repeated_ema():
    random.seed(42)
    values = [random.uniform(10, 100) for _ in range(300)]
    period = 12

    series = _ema_series(values, period)
    assert len(series) == len(values)

    for i in range(len(values)):
        expected = ema(values[:i + 1], period)
        assert math.isclose(series[i], expected, rel_tol=1e-12, abs_tol=1e-12)


def test_macd_insufficient_data():
    assert macd([10.0] * 20) == (0.0, 0.0, 0.0)


def test_macd_valid_data():
    random.seed(123)
    values = [random.uniform(50, 150) for _ in range(300)]
    line, signal, hist = macd(values)

    assert isinstance(line, float)
    assert isinstance(signal, float)
    assert isinstance(hist, float)
    assert math.isclose(hist, line - signal, rel_tol=1e-12, abs_tol=1e-12)


def test_macd_edge_cases():
    assert macd([]) == (0.0, 0.0, 0.0)
    assert macd([100.0] * 35) == (0.0, 0.0, 0.0)
