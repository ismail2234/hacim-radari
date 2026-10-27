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
from kripto_bot import compute_rsi, compute_atr, compute_adx, is_breakout, find_recent_cross_up


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


def test_compute_atr_and_adx():
    df = pd.DataFrame({
        "high": [10.0, 12.0, 11.5, 13.0, 12.8, 14.0, 13.5, 15.0, 14.5, 16.0, 15.5, 17.0, 16.5, 18.0, 17.5, 19.0],
        "low": [9.0, 10.5, 10.0, 11.5, 11.0, 12.5, 12.0, 13.5, 13.0, 14.5, 14.0, 15.5, 15.0, 16.5, 16.0, 17.5],
        "close": [9.5, 11.0, 10.5, 12.0, 11.5, 13.0, 12.5, 14.0, 13.5, 15.0, 14.5, 16.0, 15.5, 17.0, 16.5, 18.0],
    })
    atr = compute_atr(df, period=14)
    adx = compute_adx(df, period=14)
    assert not atr.iloc[-1] != atr.iloc[-1]  # check not NaN
    assert not adx.iloc[-1] != adx.iloc[-1]  # check not NaN
    assert atr.iloc[-1] > 0
    assert adx.iloc[-1] >= 0
