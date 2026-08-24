from __future__ import annotations

from typing import Any

import pandas as pd


KLINE_COLUMNS = [
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


def klines_to_dataframe(
    klines: list[list[Any]],
) -> pd.DataFrame:
    """
    Binance mum verisini temiz bir pandas DataFrame'e çevirir.
    """

    if not klines:
        return pd.DataFrame(
            columns=[
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
        )

    rows = []

    for kline in klines:
        if len(kline) < 12:
            continue

        rows.append(kline[:12])

    if not rows:
        raise ValueError("Geçerli mum verisi bulunamadı.")

    df = pd.DataFrame(
        rows,
        columns=KLINE_COLUMNS,
    )

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

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True,
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True,
    )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    df = df.sort_values("open_time")
    df = df.drop_duplicates(
        subset=["open_time"],
        keep="last",
    )

    df = df.reset_index(drop=True)

    return df


def calculate_basic_metrics(
    df: pd.DataFrame,
) -> dict[str, float]:
    """
    Son mum üzerinden temel fiyat ve hacim metriklerini hesaplar.
    """

    if df.empty:
        raise ValueError("Analiz için mum verisi boş.")

    last = df.iloc[-1]

    close = float(last["close"])
    volume = float(last["volume"])

    previous_close = (
        float(df.iloc[-2]["close"])
        if len(df) >= 2
        else close
    )

    price_change_pct = 0.0

    if previous_close > 0:
        price_change_pct = (
            (close - previous_close)
            / previous_close
            * 100
        )

    average_volume = (
        float(df["volume"].tail(20).mean())
        if len(df) >= 20
        else float(df["volume"].mean())
    )

    volume_ratio = 0.0

    if average_volume > 0:
        volume_ratio = volume / average_volume

    return {
        "price": close,
        "volume": volume,
        "price_change_pct": price_change_pct,
        "average_volume": average_volume,
        "volume_ratio": volume_ratio,
    }
