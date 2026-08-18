"""
V26 - indicators.py  (2/8)
MA, RSI, MACD, KDJ, ATR, Bollinger, hacim ozellikleri ve
konsolidasyon/sikisma tespiti bu dosyada birlesik.
"""

import numpy as np
import pandas as pd

from config import CONFIG


# ---------- Temel indikatorler ----------

def add_moving_averages(df, periods=None):
    periods = periods or CONFIG["ma_periods"]
    for p in periods:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
    return df


def add_rsi(df, period=None):
    period = period or CONFIG["rsi_period"]
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi_slope"] = df["rsi"].diff()  # sadece esik degil, yon/hiz da onemli
    return df


def add_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def add_kdj(df, period=9):
    low_min = df["low"].rolling(period).min()
    high_max = df["high"].rolling(period).max()
    rsv = (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan) * 100
    df["kdj_k"] = rsv.ewm(com=2).mean()
    df["kdj_d"] = df["kdj_k"].ewm(com=2).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]
    return df


def add_atr(df, period=None):
    period = period or CONFIG["atr_period"]
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(period).mean()
    return df


def add_bollinger(df, period=None, std_mult=2):
    period = period or CONFIG["bb_period"]
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    df["bb_upper"] = mid + std_mult * std
    df["bb_lower"] = mid - std_mult * std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / mid
    return df


def add_volume_features(df, avg_period=None):
    avg_period = avg_period or CONFIG["vol_avg_period"]
    df["vol_avg"] = df["volume"].rolling(avg_period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg"].replace(0, np.nan)
    df["price_up"] = df["close"] > df["open"]
    df["up_volume"] = np.where(df["price_up"], df["volume"], 0)
    df["down_volume"] = np.where(~df["price_up"], df["volume"], 0)
    return df


def compute_all(df):
    """Butun indikatorleri tek seferde ekler."""
    df = add_moving_averages(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_kdj(df)
    df = add_atr(df)
    df = add_bollinger(df)
    df = add_volume_features(df)
    return df


# ---------- Konsolidasyon / sikisma tespiti ----------

def detect_consolidation(df, window=20, bb_width_percentile=25, ma_converge_pct=0.03):
    """
    Bollinger genisligi son 'window' mumun en dusuk yuzdelik diliminde mi
    VE MA7-MA30 birbirine yakinsamis mi -- ikisi birden 'sikisma' sayilir.
    """
    if len(df) < window:
        return False

    recent_width = df["bb_width"].iloc[-window:]
    threshold = recent_width.quantile(bb_width_percentile / 100)
    is_tight = df["bb_width"].iloc[-1] <= threshold

    ma_gap = abs(df["ma7"].iloc[-1] - df["ma30"].iloc[-1]) / df["close"].iloc[-1]
    ma_converged = ma_gap < ma_converge_pct

    return bool(is_tight and ma_converged)


def consolidation_duration(df, tolerance_pct=0.03) -> int:
    """Fiyatin kac mumdur belirli bir bant icinde kaldigini kabaca olcer."""
    closes = df["close"].iloc[-50:]
    if len(closes) == 0:
        return 0
    band_high = closes.max()
    band_low = closes.min()
    band_width = (band_high - band_low) / closes.mean()
    if band_width > tolerance_pct * 3:
        return 0
    return len(closes)
    
