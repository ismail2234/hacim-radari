
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
    os.getenv("MAX_SIGNALS_PER_SCAN", "2")
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
    "balina_v21.db"
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


# ---------------------------------------------------------
# UZUN VADELİ TREND AYARLARI
# ---------------------------------------------------------

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
    "balina-v21"
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
        "User-Agent":
        "BalinaRadari-V21/1.0"
    })

    return session


S = build_session()


# Günlük trend verisi için cache.
# Böylece her 30 saniyelik taramada aynı günlük veriyi
# tekrar tekrar Binance TR'den istemiyoruz.
DAILY_CACHE = {}

DAILY_CACHE_LOCK = Lock()


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
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendMessage",
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

    closes = []

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


def ema(values, period):

    if not values:
        return 0.0

    if len(values) < period:
        return avg(values)

    k = 2 / (
        period + 1
    )

    result = avg(
        values[:period]
    )

    for value in values[period:]:

        result = (
            value * k
            +
            result * (1 - k)
        )

    return result


def rsi(values, period=14):

    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        diff = (
            values[i]
            -
            values[i - 1]
        )

        gains.append(
            max(diff, 0)
        )

        losses.append(
            max(-diff, 0)
        )

    average_gain = avg(
        gains[-period:]
    )

    average_loss = avg(
        losses[-period:]
    )

    if average_loss == 0:
        return 100.0

    return (
        100
        -
        100 / (
            1
            +
            average_gain
            /
            average_loss
        )
    )


def macd(values):

    if len(values) < 35:
        return 0, 0, 0

    macd_values = []

    for i in range(
        26,
        len(values) + 1
    ):

        macd_values.append(
            ema(
                values[:i],
                12
            )
            -
            ema(
                values[:i],
                26
            )
        )

    main = macd_values[-1]

    signal = ema(
        macd_values,
        9
    )

    histogram = (
        main - signal
    )

    return (
        main,
        signal,
        histogram
    )


def bb(
    values,
    period=20,
    k=2
):

    if len(values) < period:
        return 0, 0, 0

    sample = values[-period:]

    middle = avg(sample)

    deviation = (
        avg([
            (x - middle) ** 2
            for x in sample
        ])
    ) ** 0.5

    return (
        middle - k * deviation,
        middle,
        middle + k * deviation
)
