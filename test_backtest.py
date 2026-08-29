from __future__ import annotations

import pytest
import pandas as pd
from market_data import prepare_market_data
from signal_engine import V30SignalEngine
from backtest import V30Backtester


def create_mock_klines(count: int = 100) -> list:
    klines = []
    price = 100.0
    for i in range(count):
        if i % 25 == 0:
            price *= 0.93
        elif i % 25 < 6:
            price *= 1.025
        else:
            price *= 0.999
        vol = 50000.0 * (1 + (i % 7))
        if i % 25 < 6:
            vol *= 3.0
        klines.append([
            1600000000000 + i * 300000,
            str(price),
            str(price * 1.01),
            str(price * 0.99),
            str(price * 1.005),
            str(vol / price),
            1600000000000 + (i + 1) * 300000,
            str(vol),
            500,
            "500.0",
            "51000.0",
            "0",
        ])
    return klines


def test_signal_engine_idx_equivalence():
    klines = create_mock_klines(100)
    df = prepare_market_data(klines)
    engine = V30SignalEngine(min_buy_score=40.0)

    for idx in range(25, len(df)):
        res_default = engine.analyze("BTC_TRY", df.iloc[: idx + 1])
        res_idx = engine.analyze("BTC_TRY", df, idx=idx)
        assert res_default == res_idx


def test_signal_engine_insufficient_data():
    klines = create_mock_klines(10)
    df = prepare_market_data(klines)
    engine = V30SignalEngine()
    result = engine.analyze("BTC_TRY", df)
    assert result["status"] == "INSUFFICIENT_DATA"


def test_backtester_run():
    klines = create_mock_klines(100)
    backtester = V30Backtester()
    result = backtester.run("BTC_TRY", klines)

    assert result["symbol"] == "BTC_TRY"
    assert "signals" in result
    assert "signals_detail" in result
