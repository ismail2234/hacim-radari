
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

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
WORKERS = int(os.getenv("MAX_WORKERS", "12"))
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "1200"))

MIN_QUOTE_VOLUME = float(
    os.getenv("MIN_QUOTE_VOLUME_TRY", "500000")
)

SHORTLIST = int(
    os.getenv("SHORTLIST_SIZE", "120")
)

DEEP_LIMIT = int(
    os.getenv("DEEP_LIMIT", "70")
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

log = logging.getLogger("balina-v21")


def build_session():

    retry_args = dict(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.35,
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
            allowed_methods=["GET", "POST"],
            **retry_args
        )
    except TypeError:
        retry = Retry(
            method_whitelist=["GET", "POST"],
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
        "User-Agent": "BalinaRadari-V21/1.0"
    })

    return session


S = build_session()


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


def klines(symbol, interval, limit):

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


def ema(values, period):

    if not values:
        return 0.0

    if len(values) < period:
        return avg(values)

    k = 2 / (period + 1)

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

    for i in range(1, len(values)):

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

    gain = avg(gains[-period:])
    loss = avg(losses[-period:])

    if loss == 0:
        return 100.0

    return (
        100
        -
        100 / (
            1 + gain / loss
        )
    )


def macd(values):

    if len(values) < 35:
        return 0.0, 0.0, 0.0

    values_macd = []

    for i in range(
        26,
        len(values) + 1
    ):

        values_macd.append(
            ema(values[:i], 12)
            -
            ema(values[:i], 26)
        )

    main = values_macd[-1]
    signal = ema(values_macd, 9)

    return (
        main,
        signal,
        main - signal
    )


def bb(values, period=20, k=2):

    if len(values) < period:
        return 0.0, 0.0, 0.0

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


def adx(
    highs,
    lows,
    closes,
    period=14
):

    if len(closes) < period * 2 + 1:
        return 0.0, 0.0, 0.0

    tr = []
    plus = []
    minus = []

    for i in range(1, len(closes)):

        tr.append(
            max(
                highs[i] - lows[i],
                abs(
                    highs[i]
                    -
                    closes[i - 1]
                ),
                abs(
                    lows[i]
                    -
                    closes[i - 1]
                )
            )
        )

        up = (
            highs[i]
            -
            highs[i - 1]
        )

        down = (
            lows[i - 1]
            -
            lows[i]
        )

        plus.append(
            up
            if up > down and up > 0
            else 0
        )

        minus.append(
            down
            if down > up and down > 0
            else 0
        )

    atr = avg(tr[-period:])
    p = avg(plus[-period:])
    m = avg(minus[-period:])

    if atr <= 0:
        return 0.0, 0.0, 0.0

    pdi = 100 * p / atr
    mdi = 100 * m / atr

    dx = (
        100 * abs(pdi - mdi) / (pdi + mdi)
        if pdi + mdi
        else 0
    )

    return dx, pdi, mdi
