import os
import time
import sqlite3
import logging
import sys

from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


BASE = os.getenv(
    "BINANCE_TR_BASE",
    "https://api.binance.me"
)

SCAN_INTERVAL = int(
    os.getenv("SCAN_INTERVAL", "30")
)

WORKERS = int(
    os.getenv("MAX_WORKERS", "10")
)

MAX_SIGNALS = int(
    os.getenv("MAX_SIGNALS_PER_SCAN", "3")
)

COOLDOWN = int(
    os.getenv("SIGNAL_COOLDOWN", "1200")
)

MIN_QUOTE_VOLUME = float(
    os.getenv("MIN_QUOTE_VOLUME_TRY", "1000000")
)

SHORTLIST = int(
    os.getenv("SHORTLIST_SIZE", "80")
)

TIMEOUT = int(
    os.getenv("REQUEST_TIMEOUT", "8")
)

DB_PATH = os.getenv(
    "STATE_DB_PATH",
    "balina_v22.db"
)

OUTCOME_WINDOW = int(
    os.getenv("OUTCOME_WINDOW", "900")
)

TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

CHAT = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


LT30_MILD = float(
    os.getenv("LT30_MILD", "-20")
)

LT30_STRONG = float(
    os.getenv("LT30_STRONG", "-35")
)

LT90_MILD = float(
    os.getenv("LT90_MILD", "-30")
)

LT90_STRONG = float(
    os.getenv("LT90_STRONG", "-50")
)

LT90_EXTREME = float(
    os.getenv("LT90_EXTREME", "-65")
)

DAILY_CACHE_TTL = int(
    os.getenv("DAILY_CACHE_TTL", "900")
)

MIN_1M_TRADES = int(
    os.getenv("MIN_1M_TRADES", "20")
)

MIN_5M_TRADES = int(
    os.getenv("MIN_5M_TRADES", "50")
)

TRADE_REFERENCE = int(
    os.getenv("TRADE_REFERENCE", "100")
)

STREAK_WINDOW = int(
    os.getenv("STREAK_WINDOW", "180")
)

BUY_STREAK = int(
    os.getenv("BUY_STREAK", "2")
)

VERY_STREAK = int(
    os.getenv("VERY_STREAK", "2")
)

MARKET_SYMBOL = os.getenv(
    "MARKET_SYMBOL",
    "BTCTRY"
)

MARKET_MOVE = float(
    os.getenv("MARKET_MOVE", "2")
)

TOP_PRIORITY = int(
    os.getenv("TOP_PRIORITY", "5")
)

MIN_PRIORITY = int(
    os.getenv("MIN_PRIORITY", "60")
)

TRAP_BUYER = float(
    os.getenv("TRAP_BUYER", "50")
)

TRAP_VOLUME = float(
    os.getenv("TRAP_VOLUME", "1.8")
)

TRAP_MOMENTUM = float(
    os.getenv("TRAP_MOMENTUM", "-1.2")
)


EXCLUDED = {
    "USDTTRY",
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "BUSDUSDT",
    "DAIUSDT",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)

log = logging.getLogger(
    "balina-v22"
)


def build_session():

    retry_args = dict(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504
        ],
        raise_on_status=False
    )

    try:
        retry = Retry(
            allowed_methods=[
                "GET",
                "POST"
            ],
            **retry_args
        )
    except TypeError:
        retry = Retry(
            method_whitelist=[
                "GET",
                "POST"
            ],
            **retry_args
        )

    session = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=40,
        pool_maxsize=40,
        max_retries=retry
    )

    session.mount(
        "https://",
        adapter
    )

    session.headers.update({
        "User-Agent": "BalinaRadari-V22/1.0"
    })

    return session


S = build_session()

DAILY_CACHE = {}
DAILY_CACHE_LOCK = Lock()

MARKET_CACHE = {}
MARKET_CACHE_LOCK = Lock()


def api(
    path,
    params=None,
    method="GET",
    payload=None
):

    response = S.request(
        method,
        BASE + path,
        params=params,
        json=payload,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return response.json()


def telegram(text):

    if not TOKEN or not CHAT:
        return False

    try:

        response = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT,
                "text": text
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        return bool(
            response.json().get("ok")
        )

    except Exception as e:

        log.error(
            "Telegram: %s",
            e
        )

        return False


def tickers():

    try:
        return api(
            "/api/v3/ticker/24hr"
        )
    except Exception as e:
        log.error(
            "Ticker: %s",
            e
        )
        return []


def exchange_info():

    try:
        return api(
            "/api/v3/exchangeInfo"
        )
    except Exception as e:
        log.error(
            "ExchangeInfo: %s",
            e
        )
        return {}


def klines(
    symbol,
    interval,
    limit
):

    try:

        return api(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )

    except Exception as e:

        log.debug(
            "%s %s: %s",
            symbol,
            interval,
            e
        )

        return []


def avg(values):

    return (
        sum(values) / len(values)
        if values
        else 0.0
    )


def pct(a, b):

    if not a or b is None:
        return 0.0

    return (
        (b - a) / a
    ) * 100


def clamp(value):

    return max(
        0,
        min(
            100,
            int(round(value))
        )
)
