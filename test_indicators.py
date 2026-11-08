"""
Gösterge fonksiyonları için basit birim testler.

BU DOSYA RAILWAY DEPLOY'UNUN BİR PARÇASI DEĞİLDİR — Procfile bunu çalıştırmaz,
bota hiçbir etkisi yoktur. Sadece kendi bilgisayarında matematiğin doğru
çalıştığını kontrol etmek istersen buradadır.

Çalıştırmak için (kendi bilgisayarında, Railway'de değil):
    pip install pytest
    pytest test_indicators.py
"""

import pandas as pd
import numpy as np
import pytest
from kripto_bot import compute_rsi, is_breakout, find_recent_cross_up, compute_atr, compute_adx


def test_rsi_all_gains_is_near_100():
    prices = pd.Series([float(i) for i in range(1, 30)])
    rsi = compute_rsi(prices, period=14)
    assert rsi.iloc[-1] > 95


def test_rsi_all_losses_is_near_0():
    prices = pd.Series([float(i) for i in range(30, 1, -1)])
    rsi = compute_rsi(prices, period=14)
    assert rsi.iloc[-1] < 5


def test_breakout_detects_new_local_high():
    closes = [10.0] * 21 + [15.0]
    df = pd.DataFrame({"close": closes})
    assert is_breakout(df, lookback=20) is True


def test_breakout_false_when_not_new_high():
    closes = [10.0] * 21 + [9.0]
    df = pd.DataFrame({"close": closes})
    assert is_breakout(df, lookback=20) is False


def test_find_recent_cross_up_detects_crossover():
    a = pd.Series([1.0, 1.0, 2.0, 3.0])
    b = pd.Series([2.0, 2.0, 2.0, 2.0])
    found, offset = find_recent_cross_up(a, b, lookback=3)
    assert found is True
    assert offset == 0


def test_find_recent_cross_up_no_crossover():
    a = pd.Series([1.0, 1.0, 1.0, 1.0])
    b = pd.Series([2.0, 2.0, 2.0, 2.0])
    found, offset = find_recent_cross_up(a, b, lookback=3)
    assert found is False


def test_compute_atr_constant_range():
    # If high - low is constant 2.0 and close stays flat at midpoint, TR = 2.0
    n = 30
    df = pd.DataFrame({
        "high": [11.0] * n,
        "low": [9.0] * n,
        "close": [10.0] * n,
    })
    atr = compute_atr(df, period=14)
    assert len(atr) == n
    assert pd.isna(atr.iloc[12])  # period - 1 rolling mean should be NaN
    assert np.isclose(atr.iloc[13], 2.0)
    assert np.isclose(atr.iloc[-1], 2.0)


def test_compute_atr_gap_up():
    # Previous close = 10, current low = 15, high = 17 -> TR = high - prev_close = 7
    df = pd.DataFrame({
        "high": [10.0] * 14 + [17.0],
        "low": [10.0] * 14 + [15.0],
        "close": [10.0] * 14 + [16.0],
    })
    # TR for first 14 rows: high-low = 0
    # TR for 15th row: max(17-15=2, |17-10|=7, |15-10|=5) = 7
    # 14-period rolling mean at index 14 is (13*0 + 7) / 14 = 0.5
    atr = compute_atr(df, period=14)
    assert np.isclose(atr.iloc[-1], 7.0 / 14)


def test_compute_adx_returns_valid_series():
    # Trending upwards
    n = 50
    df = pd.DataFrame({
        "high": [float(i + 2) for i in range(n)],
        "low": [float(i) for i in range(n)],
        "close": [float(i + 1) for i in range(n)],
    })
    adx = compute_adx(df, period=14)
    assert len(adx) == n
    assert not pd.isna(adx.iloc[-1])
    assert 0 <= adx.iloc[-1] <= 100
