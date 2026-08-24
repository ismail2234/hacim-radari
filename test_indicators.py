import pytest
from indicators import macd, _ema_series, ema


def test_macd_short_input():
    # Input shorter than slow + signal (26 + 9 = 35) should return (0.0, 0.0, 0.0)
    values = [float(i) for i in range(10)]
    assert macd(values) == (0.0, 0.0, 0.0)


def test_macd_empty_input():
    assert macd([]) == (0.0, 0.0, 0.0)


def test_ema_series_equivalence():
    values = [10.0 + i * 0.5 for i in range(50)]
    # Compare _ema_series result against calling ema() on every prefix
    expected = [ema(values[: i + 1], 12) for i in range(len(values))]
    actual = _ema_series(values, 12)
    assert len(expected) == len(actual)
    for exp, act in zip(expected, actual):
        assert abs(exp - act) < 1e-9


def test_macd_normal_sequence():
    values = [100.0 + (i % 5) * 2.0 - (i % 3) * 1.5 for i in range(100)]
    line_val, signal_val, hist_val = macd(values)

    # Calculate expected values via prefix ema loop for exact check
    fast_vals = [ema(values[: i + 1], 12) for i in range(len(values))]
    slow_vals = [ema(values[: i + 1], 26) for i in range(len(values))]
    line = [a - b for a, b in zip(fast_vals, slow_vals)]
    expected_signal = ema(line, 9)
    expected_line = line[-1]
    expected_hist = expected_line - expected_signal

    assert abs(line_val - expected_line) < 1e-9
    assert abs(signal_val - expected_signal) < 1e-9
    assert abs(hist_val - expected_hist) < 1e-9
