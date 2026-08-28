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
    Binance kline verisini temiz bir DataFrame'e çevirir.

    Özellikle:
        quote_volume = TRY bazlı işlem hacmi
    alanını koruyoruz.
    """

    if not klines:
        return pd.DataFrame()

    rows = []

    for candle in klines:
        if not isinstance(candle, (list, tuple)):
            continue

        if len(candle) < 12:
            continue

        rows.append(candle[:12])

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        rows,
        columns=KLINE_COLUMNS,
    )

    # --------------------------------------------------------
    # Sayısal alanlar
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Zaman
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Geçersiz satırları temizle
    # --------------------------------------------------------

    df = df.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
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


def add_basic_features(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Signal Engine için temel fiyat/hacim özelliklerini oluşturur.

    Burada henüz AL kararı vermiyoruz.
    Sadece ham veriyi analiz edilebilir hale getiriyoruz.
    """

    if df.empty:
        return df.copy()

    result = df.copy()

    # --------------------------------------------------------
    # Fiyat değişimleri
    # --------------------------------------------------------

    result["price_change_1"] = (
        result["close"]
        .pct_change(1)
        * 100
    )

    result["price_change_3"] = (
        result["close"]
        .pct_change(3)
        * 100
    )

    result["price_change_5"] = (
        result["close"]
        .pct_change(5)
        * 100
    )

    # --------------------------------------------------------
    # Mum gövdesi
    # --------------------------------------------------------

    result["candle_body"] = (
        result["close"]
        - result["open"]
    )

    result["candle_body_percent"] = (
        result["candle_body"]
        / result["open"].replace(0, pd.NA)
        * 100
    )

    # --------------------------------------------------------
    # Mum aralığı
    # --------------------------------------------------------

    result["candle_range"] = (
        result["high"]
        - result["low"]
    )

    result["candle_range_percent"] = (
        result["candle_range"]
        / result["open"].replace(0, pd.NA)
        * 100
    )

    # --------------------------------------------------------
    # Üst / alt fitil
    # --------------------------------------------------------

    result["upper_wick"] = (
        result["high"]
        - result[["open", "close"]].max(axis=1)
    )

    result["lower_wick"] = (
        result[["open", "close"]].min(axis=1)
        - result["low"]
    )

    # --------------------------------------------------------
    # Hacim ortalamaları
    # --------------------------------------------------------

    result["volume_ma_5"] = (
        result["quote_volume"]
        .rolling(5)
        .mean()
    )

    result["volume_ma_10"] = (
        result["quote_volume"]
        .rolling(10)
        .mean()
    )

    result["volume_ma_20"] = (
        result["quote_volume"]
        .rolling(20)
        .mean()
    )

    # --------------------------------------------------------
    # Hacim oranları
    # --------------------------------------------------------

    result["volume_ratio_5"] = (
        result["quote_volume"]
        / result["volume_ma_5"].replace(0, pd.NA)
    )

    result["volume_ratio_20"] = (
        result["quote_volume"]
        / result["volume_ma_20"].replace(0, pd.NA)
    )

    # --------------------------------------------------------
    # Son mumlara göre hacim değişimi
    # --------------------------------------------------------

    result["volume_change_1"] = (
        result["quote_volume"]
        .pct_change(1)
        * 100
    )

    result["volume_change_3"] = (
        result["quote_volume"]
        .pct_change(3)
        * 100
    )

    # --------------------------------------------------------
    # Yerel dipten uzaklık
    # --------------------------------------------------------

    result["low_20"] = (
        result["low"]
        .rolling(20)
        .min()
    )

    result["high_20"] = (
        result["high"]
        .rolling(20)
        .max()
    )

    price_range = (
        result["high_20"]
        - result["low_20"]
    ).replace(0, pd.NA)

    result["position_in_20_range"] = (
        (
            result["close"]
            - result["low_20"]
        )
        / price_range
        * 100
    )

    return result


def prepare_market_data(
    klines: list[list[Any]],
) -> pd.DataFrame:
    """
    Kline -> temiz veri -> temel özellikler.
    """

    df = klines_to_dataframe(
        klines
    )

    if df.empty:
        return df

    return add_basic_features(
        df
  )
