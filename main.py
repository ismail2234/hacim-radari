import os
import time
import sqlite3
import logging
import sys

from threading import Thread, Lock
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


# =========================================================
# V22 | ANA KONFİGÜRASYON
# =========================================================

BASE = os.getenv(
    "BINANCE_TR_BASE",
    "https://api.binance.me"
)

SCAN_INTERVAL = int(
    os.getenv(
        "SCAN_INTERVAL",
        "30"
    )
)

WORKERS = int(
    os.getenv(
        "MAX_WORKERS",
        "10"
    )
)

MAX_SIGNALS = int(
    os.getenv(
        "MAX_SIGNALS_PER_SCAN",
        "5"
    )
)

COOLDOWN = int(
    os.getenv(
        "SIGNAL_COOLDOWN",
        "1200"
    )
)

MIN_QUOTE_VOLUME = float(
    os.getenv(
        "MIN_QUOTE_VOLUME_TRY",
        "1000000"
    )
)

SHORTLIST = int(
    os.getenv(
        "SHORTLIST_SIZE",
        "80"
    )
)

TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "8"
    )
)

DB_PATH = os.getenv(
    "STATE_DB_PATH",
    "balina_v22.db"
)

OUTCOME_WINDOW = int(
    os.getenv(
        "OUTCOME_WINDOW",
        "900"
    )
)


# =========================================================
# TELEGRAM
# =========================================================

TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

CHAT = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# =========================================================
# V22 | UZUN VADELİ TREND EŞİKLERİ
#
# Bütün eşikler tek yerden okunuyor.
# message() içinde sabit -20 / -50 gibi
# gizli değer kullanılmayacak.
# =========================================================

LONG_TERM_CACHE_TTL = int(
    os.getenv(
        "LONG_TERM_CACHE_TTL",
        "900"
    )
)

LT30_MILD = float(
    os.getenv(
        "LT30_MILD",
        "-20"
    )
)

LT30_STRONG = float(
    os.getenv(
        "LT30_STRONG",
        "-35"
    )
)

LT90_MILD = float(
    os.getenv(
        "LT90_MILD",
        "-30"
    )
)

LT90_STRONG = float(
    os.getenv(
        "LT90_STRONG",
        "-50"
    )
)

LT90_EXTREME = float(
    os.getenv(
        "LT90_EXTREME",
        "-65"
    )
)


# =========================================================
# V22 | TRADE COUNT GÜVEN EŞİKLERİ
# =========================================================

MIN_1M_TRADES = int(
    os.getenv(
        "MIN_1M_TRADES",
        "20"
    )
)

MIN_5M_TRADES = int(
    os.getenv(
        "MIN_5M_TRADES",
        "50"
    )
)

TRADE_COUNT_REFERENCE = int(
    os.getenv(
        "TRADE_COUNT_REFERENCE",
        "100"
    )
)


# =========================================================
# V22 | STREAK / SÜREKLİLİK
# =========================================================

STREAK_WINDOW = int(
    os.getenv(
        "STREAK_WINDOW",
        "180"
    )
)

BUY_STREAK_REQUIRED = int(
    os.getenv(
        "BUY_STREAK_REQUIRED",
        "2"
    )
)

VERY_STREAK_REQUIRED = int(
    os.getenv(
        "VERY_STREAK_REQUIRED",
        "2"
    )
)


# =========================================================
# V22 | BTC/TRY PİYASA FİLTRESİ
# =========================================================

MARKET_SYMBOL = os.getenv(
    "MARKET_SYMBOL",
    "BTCTRY"
)

MARKET_MOMENTUM_MINUTES = int(
    os.getenv(
        "MARKET_MOMENTUM_MINUTES",
        "15"
    )
)

MARKET_STRONG_MOVE = float(
    os.getenv(
        "MARKET_STRONG_MOVE",
        "2.0"
    )
)

MARKET_EXTREME_MOVE = float(
    os.getenv(
        "MARKET_EXTREME_MOVE",
        "4.0"
    )
)


# =========================================================
# V22 | ÖNCELİK SIRALAMASI
# =========================================================

TOP_PRIORITY_COUNT = int(
    os.getenv(
        "TOP_PRIORITY_COUNT",
        "5"
    )
)

PRIORITY_MIN_SCORE = int(
    os.getenv(
        "PRIORITY_MIN_SCORE",
        "60"
    )
)


# =========================================================
# V22 | GİRİŞ KALİTESİ
# =========================================================

ENTRY_RSI_HIGH = float(
    os.getenv(
        "ENTRY_RSI_HIGH",
        "78"
    )
)

ENTRY_RSI_EXTREME = float(
    os.getenv(
        "ENTRY_RSI_EXTREME",
        "88"
    )
)

ENTRY_MOMENTUM_HIGH = float(
    os.getenv(
        "ENTRY_MOMENTUM_HIGH",
        "2.5"
    )
)

ENTRY_MOMENTUM_EXTREME = float(
    os.getenv(
        "ENTRY_MOMENTUM_EXTREME",
        "5.0"
    )
)


# =========================================================
# V22 | TRAP
# =========================================================

TRAP_BUYER_LOW = float(
    os.getenv(
        "TRAP_BUYER_LOW",
        "50"
    )
)

TRAP_VOLUME_HIGH = float(
    os.getenv(
        "TRAP_VOLUME_HIGH",
        "1.8"
    )
)

TRAP_MOMENTUM_NEGATIVE = float(
    os.getenv(
        "TRAP_MOMENTUM_NEGATIVE",
        "-1.2"
    )
)


# =========================================================
# V22 | DIŞARIDA BIRAKILACAK MARKETLER
# =========================================================

EXCLUDED = {
    "USDTTRY",
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "BUSDUSDT",
    "DAIUSDT",
}


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    ),
    stream=sys.stdout
)

log = logging.getLogger(
    "balina-v22"
)


# =========================================================
# HTTP SESSION
# =========================================================

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
        "User-Agent":
            "BalinaRadari-V22/1.0"
    })

    return session


S = build_session()


# =========================================================
# V22 | CACHE KİLİTLERİ
# =========================================================

DAILY_CACHE = {}

DAILY_CACHE_LOCK = Lock()


MARKET_CACHE = {}

MARKET_CACHE_LOCK = Lock()


# =========================================================
# API
# =========================================================

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


# =========================================================
# TELEGRAM
# =========================================================

def telegram(text):

    if not TOKEN or not CHAT:

        return False

    try:

        response = S.post(
            (
                "https://api.telegram.org/"
                f"bot{TOKEN}/sendMessage"
            ),
            json={
                "chat_id": CHAT,
                "text": text
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        return bool(
            response.json().get(
                "ok"
            )
        )

    except Exception as e:

        log.error(
            "Telegram: %s",
            e
        )

        return False


# =========================================================
# MARKET VERİLERİ
# =========================================================

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


# =========================================================
# TEMEL MATEMATİK
# =========================================================

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
            int(
                round(value)
            )
        )
    )


# =========================================================
# V22 | GÜNLÜK TREND
#
# ÖNEMLİ:
# Bu fonksiyon analyze() başında çağrılmayacak.
# Önce 5m gate çalışacak.
# Böylece ölü adaylarda gereksiz günlük API
# isteği yapılmayacak.
# =========================================================

def daily_trend(symbol):

    now = time.time()

    with DAILY_CACHE_LOCK:

        cached = DAILY_CACHE.get(
            symbol
        )

        if cached:

            cached_time, cached_data = cached

            if (
                now - cached_time
                <
                LONG_TERM_CACHE_TTL
            ):

                return cached_data


    data = klines(
        symbol,
        "1d",
        100
    )

    if len(data) < 92:

        result = {
            "ok": False,
            "d30": 0.0,
            "d90": 0.0
        }

        with DAILY_CACHE_LOCK:

            DAILY_CACHE[symbol] = (
                now,
                result
            )

        return result


    closed = data[:-1]

    try:

        closes = [
            float(x[4])
            for x in closed
        ]

    except (
        TypeError,
        ValueError
    ):

        return {
            "ok": False,
            "d30": 0.0,
            "d90": 0.0
        }


    if len(closes) < 91:

        return {
            "ok": False,
            "d30": 0.0,
            "d90": 0.0
        }


    current = closes[-1]

    base_30 = closes[-31]

    base_90 = closes[-91]

    d30 = pct(
        base_30,
        current
    )

    d90 = pct(
        base_90,
        current
    )

    result = {
        "ok": True,
        "d30": d30,
        "d90": d90
    }

    with DAILY_CACHE_LOCK:

        DAILY_CACHE[symbol] = (
            now,
            result
        )

    return result
