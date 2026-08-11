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


MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

PREP_THRESHOLD = int(os.getenv("PREP_THRESHOLD", "68"))
STRONG_THRESHOLD = int(os.getenv("STRONG_THRESHOLD", "80"))
ROCKET_THRESHOLD = int(os.getenv("ROCKET_THRESHOLD", "90"))

MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "3"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "300"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))

DB_PATH = os.getenv("STATE_DB_PATH", "balina_v17.db")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {
    "BTCUSDT",
    "ETHUSDT",
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "USDPUSDT",
    "DAIUSDT",
    "BUSDUSDT",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

log = logging.getLogger("balina-v17")


def build_session():
    retry_kwargs = dict(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )

    try:
        retry = Retry(
            allowed_methods=["GET", "POST"],
            **retry_kwargs,
        )
    except TypeError:
        retry = Retry(
            method_whitelist=["GET", "POST"],
            **retry_kwargs,
        )

    session = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20,
        max_retries=retry,
    )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "User-Agent": "BalinaRadari-V17/1.0"
    })

    return session


S = build_session()


def api(base, path, params=None):
    response = S.get(
        base + path,
        params=params,
        timeout=TIMEOUT,
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
                "text": text,
            },
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        return bool(
            response.json().get("ok")
        )

    except Exception as exc:
        log.error("Telegram: %s", exc)
        return False


def tickers(base):
    try:
        path = (
            "/api/v3/ticker/24hr"
            if base == SPOT
            else "/fapi/v1/ticker/24hr"
        )

        return api(base, path)

    except Exception as exc:
        log.error("Ticker: %s", exc)
        return []


def klines(base, symbol, interval, limit):
    try:
        path = (
            "/api/v3/klines"
            if base == SPOT
            else "/fapi/v1/klines"
        )

        return api(
            base,
            path,
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
        )

    except Exception as exc:
        log.debug(
            "%s %s: %s",
            symbol,
            interval,
            exc,
        )

        return []


def open_interest(symbol):
    try:
        data = api(
            FUT,
            "/fapi/v1/openInterest",
            {"symbol": symbol},
        )

        return float(data["openInterest"])

    except Exception:
        return None


def pct(a, b):
    if a and a > 0 and b is not None:
        return (b - a) / a * 100

    return 0.0


def avg(values):
    return (
        sum(values) / len(values)
        if values
        else 0.0
    )


def clamp(value):
    return max(
        0,
        min(
            100,
            int(round(value)),
        ),
)
