"""
🐋 HACİM RADARI — GEÇMİŞ VERİ İNDİRİCİ
=====================================

Binance TR 5 dakikalık geçmiş mumlarını indirir.

Çıktı:
    SYMBOL_5m.csv

Örnek:
    SENT_TRY_5m.csv

Kullanım:
    python historical_data.py
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


# ============================================================
# AYARLAR
# ============================================================

SYMBOL = "SENT_TRY"

INTERVAL = "5m"

DAYS = 30

OUTPUT_FILE = f"{SYMBOL}_5m.csv"

LIMIT = 1000

API_URL = (
    "https://api.binance.com/api/v3/klines"
)


# ============================================================
# VERİ ÇEK
# ============================================================

def fetch_klines(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
) -> list:

    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": LIMIT,
    }

    response = requests.get(
        API_URL,
        params=params,
        timeout=20,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# GEÇMİŞ VERİYİ TOPLA
# ============================================================

def download_history() -> pd.DataFrame:

    now = datetime.now(
        timezone.utc
    )

    start = (
        now
        - timedelta(days=DAYS)
    )

    start_ms = int(
        start.timestamp() * 1000
    )

    end_ms = int(
        now.timestamp() * 1000
    )

    all_rows = []

    current_start = start_ms

    print()
    print("=" * 60)
    print("🐋 BINANCE 5M GEÇMİŞ VERİ")
    print("=" * 60)

    print(
        f"Coin : {SYMBOL}"
    )

    print(
        f"Süre : {DAYS} gün"
    )

    print()

    while current_start < end_ms:

        print(
            "Veri çekiliyor:",
            datetime.fromtimestamp(
                current_start / 1000,
                timezone.utc,
            ),
        )

        rows = fetch_klines(
            SYMBOL,
            INTERVAL,
            current_start,
            end_ms,
        )

        if not rows:
            break

        all_rows.extend(rows)

        last_open_time = int(
            rows[-1][0]
        )

        next_start = (
            last_open_time
            + 5 * 60 * 1000
        )

        if next_start <= current_start:
            break

        current_start = next_start

        time.sleep(0.15)

        if len(rows) < LIMIT:
            break

    if not all_rows:

        raise RuntimeError(
            "Hiç veri alınamadı."
        )

    # ========================================================
    # DATAFRAME
    # ========================================================

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "number_of_trades",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]

    df = pd.DataFrame(
        all_rows,
        columns=columns,
    )

    # ========================================================
    # SAYISAL KOLONLAR
    # ========================================================

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
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
        utc=True,
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True,
    )

    # ========================================================
    # TEMİZLE
    # ========================================================

    df = (
        df
        .drop_duplicates(
            subset=["open_time"]
        )
        .sort_values(
            "open_time"
        )
        .reset_index(
            drop=True
        )
    )

    # Eksik OHLC satırlarını çıkar.
    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    return df


# ============================================================
# CSV
# ============================================================

def save_history(
    df: pd.DataFrame,
) -> None:

    df.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 60)
    print("VERİ TAMAMLANDI")
    print("=" * 60)

    print(
        f"Mum sayısı : {len(df)}"
    )

    print(
        f"İlk mum    : {df['open_time'].iloc[0]}"
    )

    print(
        f"Son mum    : {df['open_time'].iloc[-1]}"
    )

    print(
        f"Dosya      : {OUTPUT_FILE}"
    )

    print("=" * 60)


# ============================================================
# ANA
# ============================================================

def main() -> None:

    try:

        df = download_history()

        save_history(df)

    except Exception as exc:

        print()
        print("❌ HATA")
        print(exc)


if __name__ == "__main__":
    main()
