import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread

from flask import Flask

from config import SETTINGS
from binance_client import BinanceClient
from scoring import analyze, rank_signals


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("v27")

client = BinanceClient(SETTINGS)


BAD_SYMBOLS = {
    "USDTTRY",
    "USDCTRY",
    "BUSDTRY",
    "FDUSDTRY",
    "TUSDTRY",
    "DAITRY",
}


def get_try_symbols():

    info = client.exchange_info()

    result = []

    for item in info.get("symbols", []):

        symbol = str(
            item.get("symbol", "")
        ).upper()

        if not symbol.endswith("TRY"):
            continue

        if symbol in BAD_SYMBOLS:
            continue

        status = str(
            item.get("status", "")
        ).upper()

        if status and status != "TRADING":
            continue

        result.append(symbol)

    return result


def get_candidates():

    data = client.tickers()

    result = []

    for item in data:

        symbol = str(
            item.get("symbol", "")
        ).upper()

        if not symbol.endswith("TRY"):
            continue

        if symbol in BAD_SYMBOLS:
            continue

        try:

            volume = float(
                item.get(
                    "quoteVolume",
                    0,
                )
            )

            price = float(
                item.get(
                    "lastPrice",
                    0,
                )
            )

            change = float(
                item.get(
                    "priceChangePercent",
                    0,
                )
            )

        except:

            continue

        if price <= 0:
            continue

        if volume < SETTINGS.min_volume:
            continue

        if change > 25:
            continue

        result.append(
            {
                "symbol": symbol,
                "volume": volume,
                "change": change,
                "price": price,
            }
        )

    result.sort(
        key=lambda x:
        x["volume"]
        * (
            1
            + max(
                x["change"],
                0,
            )
            / 100
        ),
        reverse=True,
    )

    return result[
        :SETTINGS.shortlist
    ]


def analyze_one(item):

    try:

        return analyze(
            SETTINGS,
            client,
            item["symbol"],
        )

    except Exception as e:

        log.error(
            "%s: %s",
            item["symbol"],
            e,
        )

        return None
