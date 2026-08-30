import numpy as np
import pandas as pd
import pytest

from market_data import add_basic_features, prepare_market_data, klines_to_dataframe


def test_add_basic_features_empty():
    df = pd.DataFrame()
    result = add_basic_features(df)
    assert result.empty


def test_add_basic_features_calculations():
    mock_klines = []
    for i in range(30):
        open_time = 1600000000000 + i * 300000
        close_time = open_time + 299999
        o = 100.0 + i
        h = o + 5.0
        l = o - 2.0
        c = o + 2.0
        v = 1000.0 + i * 10
        qv = v * c
        trades = 50
        tbv = v / 2
        tbqv = qv / 2
        mock_klines.append([
            open_time, str(o), str(h), str(l), str(c), str(v),
            close_time, str(qv), trades, str(tbv), str(tbqv), "0"
        ])

    df = klines_to_dataframe(mock_klines)
    result = add_basic_features(df)

    # Check calculated columns exist
    expected_cols = [
        "price_change_1", "price_change_3", "price_change_5",
        "candle_body", "candle_body_percent",
        "candle_range", "candle_range_percent",
        "upper_wick", "lower_wick",
        "volume_ma_5", "volume_ma_10", "volume_ma_20",
        "volume_ratio_5", "volume_ratio_20",
        "volume_change_1", "volume_change_3",
        "low_20", "high_20", "position_in_20_range"
    ]
    for col in expected_cols:
        assert col in result.columns

    # Verify specific calculations on row 10
    row = result.iloc[10]
    expected_body = row["close"] - row["open"]
    assert pytest.approx(row["candle_body"]) == expected_body

    expected_upper_wick = row["high"] - max(row["open"], row["close"])
    assert pytest.approx(row["upper_wick"]) == expected_upper_wick

    expected_lower_wick = min(row["open"], row["close"]) - row["low"]
    assert pytest.approx(row["lower_wick"]) == expected_lower_wick


def test_prepare_market_data():
    mock_klines = [
        [1600000000000 + i * 300000, "100", "105", "98", "102", "1000",
         1600000000000 + i * 300000 + 299999, "102000", 50, "500", "51000", "0"]
        for i in range(25)
    ]
    df = prepare_market_data(mock_klines)
    assert not df.empty
    assert len(df) == 25
