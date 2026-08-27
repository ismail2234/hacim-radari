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


def test_indicators_basic():
    np.random.seed(42)
    df = pd.DataFrame(
        {
            "open": np.random.rand(100) * 100 + 10,
            "high": np.random.rand(100) * 100 + 15,
            "low": np.random.rand(100) * 100 + 5,
            "close": np.random.rand(100) * 100 + 10,
            "volume": np.random.rand(100) * 1000 + 100,
        }
    )

    ich = add_ichimoku(df)
    assert "ichimoku_conversion" in ich.columns
    assert "ichimoku_base" in ich.columns

    df_ema = df.copy()
    df_ema["ema_7"] = calculate_ema(df["close"], 7)
    struct = add_structure(df_ema)
    assert "recent_low" in struct.columns
    assert "curve_up" in struct.columns

    fib = add_fibonacci(df)
    assert "fib_382" in fib.columns
    assert "fib_zone" in fib.columns

    vp = add_volume_profile(df)
    assert "volume_profile_level" in vp.columns
    assert "volume_profile_support" in vp.columns

    res = add_indicators(df)
    assert len(res.columns) == len(df.columns) + 23


def test_volume_profile_flat():
    df_flat = pd.DataFrame(
        {
            "open": [10.0] * 50,
            "high": [10.0] * 50,
            "low": [10.0] * 50,
            "close": [10.0] * 50,
            "volume": [100.0] * 50,
        }
    )
    vp = add_volume_profile(df_flat)
    assert (vp["volume_profile_support"] == False).all()
