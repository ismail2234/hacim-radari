"""
🐋 HACİM RADARI
BINANCE TR GEÇMİŞ VERİ MOTORU

Amaç:
Binance TR'deki TRY paritelerini bulmak ve
5 dakikalık geçmiş mumları CSV olarak kaydetmek.

İlk test:
30 günlük veri

ÇIKTI:
data/SYMBOL_5m.csv
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


# ============================================================
# AYARLAR
# ============================================================

BASE_URL = "https://api.binance.me"

SYMBOL_ENDPOINT = (
    "/api/v3/exchangeInfo"
)

KLINE_ENDPOINT = (
    "/api/v3/klines"
)

INTERVAL = "5m"

DAYS = 30

LIMIT = 1000

DATA_DIR = "data"

REQUEST_TIMEOUT = 20

SLEEP_SECONDS = 0.15


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "Hacim-Radari/1.0"
        )
    }
)


# ============================================================
# API
# ============================================================

def api_get(
    endpoint: str,
    params: dict | None = None,
):

    url = (
        BASE_URL
        + endpoint
    )

    response = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# SEMBOLLERİ BUL
# ============================================================

def get_try_symbols() -> list[str]:

    print()
    print("=" * 60)
    print("🐋 BINANCE TR TRY PARİTELERİ")
    print("=" * 60)

    data = api_get(
        SYMBOL_ENDPOINT
    )

    symbols = []

    # --------------------------------------------------------
    # Binance benzeri exchangeInfo yapısı
    # --------------------------------------------------------

    if isinstance(data, dict):

        raw_symbols = data.get(
            "symbols",
            []
        )

    else:

        raw_symbols = []

    for item in raw_symbols:

        if not isinstance(
            item,
            dict,
        ):
            continue

        status = str(
            item.get(
                "status",
                ""
            )
        ).upper()

        quote_asset = str(
            item.get(
                "quoteAsset",
                ""
            )
        ).upper()

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).upper()

        if (
            quote_asset == "TRY"
            and status in (
                "",
                "TRADING",
            )
            and symbol
        ):

            symbols.append(
                symbol
            )

    symbols = sorted(
        set(symbols)
    )

    print(
        f"TRY parite sayısı: "
        f"{len(symbols)}"
    )

    if symbols:

        print(
            "İlk 20:"
        )

        for symbol in symbols[:20]:

            print(
                " ",
                symbol
            )

    return symbols


# ============================================================
# TEK COIN MUM VERİSİ
# ============================================================

def download_symbol(
    symbol: str,
    days: int = DAYS,
) -> pd.DataFrame:

    end_time = datetime.now(
        timezone.utc
    )

    start_time = (
        end_time
        - timedelta(
            days=days
        )
    )

    start_ms = int(
        start_time.timestamp()
        * 1000
    )

    end_ms = int(
        end_time.timestamp()
        * 1000
    )

    rows = []

    current_start = start_ms

    while (
        current_start
        < end_ms
    ):

        params = {

            "symbol": symbol,

            "interval": INTERVAL,

            "startTime":
                current_start,

            "endTime":
                end_ms,

            "limit":
                LIMIT,
        }

        data = api_get(
            KLINE_ENDPOINT,
            params,
        )

        if not data:

            break

        rows.extend(
            data
        )

        last_open_time = int(
            data[-1][0]
        )

        next_start = (
            last_open_time
            + 1
        )

        if (
            next_start
            <= current_start
        ):

            break

        current_start = (
            next_start
        )

        if len(data) < LIMIT:

            break

        time.sleep(
            SLEEP_SECONDS
        )

    if not rows:

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

        "number_of_trades",

        "taker_buy_base_volume",

        "taker_buy_quote_volume",

        "ignore",
    ]

    df = pd.DataFrame(
        rows,
        columns=columns,
    )

    # --------------------------------------------------------
    # NUMERIC
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    df["open_time"] = (
        pd.to_datetime(
            df["open_time"],
            unit="ms",
            utc=True,
        )
    )

    df["close_time"] = (
        pd.to_datetime(
            df["close_time"],
            unit="ms",
            utc=True,
        )
    )

    # --------------------------------------------------------
    # TEMİZLİK
    # --------------------------------------------------------

    df = (
        df
        .drop_duplicates(
            subset=[
                "open_time"
            ]
        )
        .sort_values(
            "open_time"
        )
        .reset_index(
            drop=True
        )
    )

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
# CSV KAYDET
# ============================================================

def save_symbol(
    symbol: str,
    df: pd.DataFrame,
) -> str:

    os.makedirs(
        DATA_DIR,
        exist_ok=True,
    )

    filename = os.path.join(
        DATA_DIR,
        f"{symbol}_5m.csv",
    )

    df.to_csv(
        filename,
        index=False,
        encoding="utf-8-sig",
    )

    return filename


# ============================================================
# TEK COIN TEST
# ============================================================

def test_one_symbol(
    symbol: str,
) -> None:

    print()
    print(
        f"Veri çekiliyor: {symbol}"
    )

    try:

        df = download_symbol(
            symbol
        )

    except Exception as exc:

        print(
            f"❌ {symbol} hata: "
            f"{exc}"
        )

        return

    if df.empty:

        print(
            f"⚠️ {symbol}: "
            "veri yok"
        )

        return

    filename = save_symbol(
        symbol,
        df,
    )

    print(
        f"✅ {symbol}"
    )

    print(
        f"   Mum: {len(df)}"
    )

    print(
        f"   İlk: "
        f"{df['open_time'].iloc[0]}"
    )

    print(
        f"   Son: "
        f"{df['open_time'].iloc[-1]}"
    )

    print(
        f"   Dosya: {filename}"
    )


# ============================================================
# TÜM TRY PARİTELERİ
# ============================================================

def download_all_try():

    symbols = get_try_symbols()

    if not symbols:

        print()
        print(
            "❌ TRY paritesi bulunamadı."
        )

        return

    print()
    print(
        f"Toplam {len(symbols)} "
        "parite işlenecek."
    )

    print()

    success = 0

    failed = 0

    for number, symbol in enumerate(
        symbols,
        start=1,
    ):

        print(
            f"[{number}/{len(symbols)}] "
            f"{symbol}"
        )

        try:

            df = download_symbol(
                symbol
            )

            if df.empty:

                print(
                    "   ⚠️ Veri yok"
                )

                failed += 1

                continue

            filename = save_symbol(
                symbol,
                df,
            )

            print(
                f"   ✅ {len(df)} mum"
            )

            print(
                f"   → {filename}"
            )

            success += 1

        except Exception as exc:

            print(
                f"   ❌ HATA: {exc}"
            )

            failed += 1

        time.sleep(
            SLEEP_SECONDS
        )

    print()
    print("=" * 60)
    print("📊 VERİ İNDİRME SONUCU")
    print("=" * 60)

    print(
        f"Başarılı : {success}"
    )

    print(
        f"Hatalı   : {failed}"
    )

    print(
        f"Toplam   : {len(symbols)}"
    )

    print(
        f"Klasör   : {DATA_DIR}/"
    )

    print("=" * 60)


# ============================================================
# ANA
# ============================================================

def main():

    print()
    print("=" * 60)
    print(
        "🐋 HACİM RADARI"
    )
    print(
        "BINANCE TR TARİHSEL VERİ MOTORU"
    )
    print("=" * 60)

    # --------------------------------------------------------
    # İLK TESTTE SADECE SENT
    # --------------------------------------------------------

    print()
    print(
        "İLK TEST: SENT/TRY"
    )

    print(
        "DAYS =",
        DAYS
    )

    test_one_symbol(
        "SENTTRY"
    )

    print()
    print(
        "İlk coin testi tamamlandı."
    )

    print(
        "Sonraki aşamada tüm TRY "
        "paritelerini indireceğiz."
    )


if __name__ == "__main__":

    main()
