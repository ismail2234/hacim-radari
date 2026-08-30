from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


KLINE_COLUMNS = [
    "open_time",
    "open",
    "open_price",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]


def klines_to_dataframe(
    klines: list[list[Any]],
) -> pd.DataFrame:

    if not klines:
        return pd.DataFrame()

    rows = []

    for candle in klines:

        if not isinstance(
            candle,
            (list, tuple),
        ):
            continue

        if len(candle) < 12:
            continue

        rows.append(
            list(candle)
        )

    if not rows:
        return pd.DataFrame()

    # Binance TR kline formatını
    # gerçek uzunluğuna göre ele al.
    #
    # Standart yapı:
    #
    # [time, open, high, low, close,
    #  volume, close_time, quote_volume,
    #  trades, ...]
    #
    # Bazı Binance TR cevaplarında
    # ek alan bulunabilir.

    normalized = []

    for row in rows:

        if len(row) >= 12:

            normalized.append(
                [
                    row[0],   # open_time
                    row[1],   # open
                    row[2],   # high
                    row[3],   # low
                    row[4],   # close
                    row[5],   # volume
                    row[6],   # close_time
                    row[7],   # quote_volume
                    row[8],   # trade_count
                    row[9],   # taker_buy_base
                    row[10],  # taker_buy_quote
                    row[11],  # ignore
                ]
            )

    if not normalized:
        return pd.DataFrame()

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trade_count",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]

    df = pd.DataFrame(
        normalized,
        columns=columns,
    )

    # ========================================================
    # SAYISAL ALANLAR
    # ========================================================

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ]

    for column in numeric_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    # ========================================================
    # ZAMAN
    # ========================================================

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        errors="coerce",
        utc=True,
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        errors="coerce",
        utc=True,
    )

    # ========================================================
    # TEMİZLİK
    # ========================================================

    df = df.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "quote_volume",
        ]
    )

    df = df.sort_values(
        "open_time"
    )

    df = df.drop_duplicates(
        subset=["open_time"],
        keep="last",
    )

    df = df.reset_index(
        drop=True
    )

    return df


# ============================================================
# TEMEL ÖZELLİKLER
# ============================================================

def add_basic_features(
    df: pd.DataFrame,
) -> pd.DataFrame:

    if df.empty:
        return df.copy()

    result = df.copy()

    # Performance optimization: cache series references to reduce dict lookups and index overhead
    close = result["close"]
    open_col = result["open"]
    high = result["high"]
    low = result["low"]
    qv = result["quote_volume"]

    # --------------------------------------------------------
    # Fiyat değişimleri
    # --------------------------------------------------------

    result["price_change_1"] = (
        close.pct_change(1) * 100
    )

    result["price_change_3"] = (
        close.pct_change(3) * 100
    )

    result["price_change_5"] = (
        close.pct_change(5) * 100
    )

    # --------------------------------------------------------
    # Mum gövdesi ve aralığı
    # --------------------------------------------------------

    candle_body = close - open_col
    result["candle_body"] = candle_body

    open_nonzero = open_col.replace(
        0, np.nan
    )

    result["candle_body_percent"] = (
        candle_body / open_nonzero * 100
    )

    candle_range = high - low
    result["candle_range"] = candle_range

    result["candle_range_percent"] = (
        candle_range / open_nonzero * 100
    )

    # --------------------------------------------------------
    # Fitiller
    # Performance optimization: replace row-wise max/min (axis=1) with
    # element-wise NumPy vectorized np.maximum/np.minimum (~3x speedup)
    # --------------------------------------------------------

    open_arr = open_col.to_numpy()
    close_arr = close.to_numpy()

    result["upper_wick"] = (
        high - np.maximum(open_arr, close_arr)
    )

    result["lower_wick"] = (
        np.minimum(open_arr, close_arr) - low
    )

    # --------------------------------------------------------
    # TRY HACİM ORTALAMALARI
    # --------------------------------------------------------

    v_ma5 = qv.rolling(5).mean()
    v_ma10 = qv.rolling(10).mean()
    v_ma20 = qv.rolling(20).mean()

    result["volume_ma_5"] = v_ma5
    result["volume_ma_10"] = v_ma10
    result["volume_ma_20"] = v_ma20

    # --------------------------------------------------------
    # HACİM ORANLARI
    # --------------------------------------------------------

    result["volume_ratio_5"] = (
        qv / v_ma5.replace(0, np.nan)
    )

    result["volume_ratio_20"] = (
        qv / v_ma20.replace(0, np.nan)
    )

    # --------------------------------------------------------
    # HACİM DEĞİŞİMİ
    # --------------------------------------------------------

    result["volume_change_1"] = (
        qv.pct_change(1) * 100
    )

    result["volume_change_3"] = (
        qv.pct_change(3) * 100
    )

    # --------------------------------------------------------
    # 20 MUMLUK DİP / TEPE
    # --------------------------------------------------------

    low_20 = low.rolling(20).min()
    high_20 = high.rolling(20).max()

    result["low_20"] = low_20
    result["high_20"] = high_20

    price_range = (
        high_20 - low_20
    ).replace(0, np.nan)

    result["position_in_20_range"] = (
        (close - low_20)
        / price_range
        * 100
    )

    return result


# ============================================================
# TEK FONKSİYONDA HAZIRLA
# ============================================================

def prepare_market_data(
    klines: list[list[Any]],
) -> pd.DataFrame:

    df = klines_to_dataframe(
        klines
    )

    if df.empty:
        return df

    return add_basic_features(
        df
    )
