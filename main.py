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


# =========================================================
# 🐋 BALİNA RADARI V20
# Binance TR ana veri kaynağı
# Telegram sadece AL / ÇOK GÜÇLÜ AL
# =========================================================

VERSION = "V20"

# ---------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------

MIN_VOLUME_TRY = float(os.getenv("MIN_VOLUME_TRY", "5000000"))
MIN_VOLUME_USDT = float(os.getenv("MIN_VOLUME_USDT", "500000"))

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "45"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

MAX_ALERTS = int(os.getenv("MAX_ALERTS_PER_SCAN", "2"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "1800"))

TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))

DB_PATH = os.getenv("STATE_DB_PATH", "balina_v20.db")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# Binance TR
TR = "https://api.binance.me"

# Global Futures sadece yardımcı veri için.
FUT = "https://fapi.binance.com"

# ---------------------------------------------------------
# GENEL AYARLAR
# ---------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)

log = logging.getLogger("balina-v20")


# ---------------------------------------------------------
# HTTP SESSION
# ---------------------------------------------------------

def build_session():

    retry_args = dict(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
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
        pool_connections=30,
        pool_maxsize=30,
        max_retries=retry
    )

    session.mount("https://", adapter)

    session.headers.update({
        "User-Agent": "BalinaRadari-V20/1.0"
    })

    return session


S = build_session()


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

def api(base, path, params=None):

    r = S.get(
        base + path,
        params=params,
        timeout=TIMEOUT
    )

    r.raise_for_status()

    return r.json()


def telegram(text):

    if not TOKEN or not CHAT:
        return False

    try:

        r = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT,
                "text": text
            },
            timeout=TIMEOUT
        )

        r.raise_for_status()

        return bool(r.json().get("ok"))

    except Exception as e:

        log.error("Telegram: %s", e)

        return False


# ---------------------------------------------------------
# BINANCE TR VERİLERİ
# ---------------------------------------------------------

def tr_exchange_info():

    try:

        return api(
            TR,
            "/api/v3/exchangeInfo"
        )

    except Exception as e:

        log.error("TR exchangeInfo: %s", e)

        return {}


def tr_tickers():

    try:

        return api(
            TR,
            "/api/v3/ticker/24hr"
        )

    except Exception as e:

        log.error("TR ticker: %s", e)

        return []


def tr_klines(symbol, interval, limit):

    try:

        return api(
            TR,
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )

    except Exception as e:

        log.debug(
            "Kline %s %s: %s",
            symbol,
            interval,
            e
        )

        return []


# ---------------------------------------------------------
# GLOBAL FUTURES - SADECE YARDIMCI
# ---------------------------------------------------------

def futures_oi(symbol):

    try:

        return float(
            api(
                FUT,
                "/fapi/v1/openInterest",
                {"symbol": symbol}
            )["openInterest"]
        )

    except Exception:

        return None


# ---------------------------------------------------------
# MATEMATİK
# ---------------------------------------------------------

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


def clamp(x):

    return max(
        0,
        min(
            100,
            int(round(x))
        )
    )


def ema(values, n):

    if len(values) < n:
        return avg(values)

    k = 2 / (n + 1)

    e = avg(values[:n])

    for x in values[n:]:
        e = x * k + e * (1 - k)

    return e


def rsi(values, n=14):

    if len(values) < n + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):

        d = values[i] - values[i - 1]

        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    ag = avg(gains[-n:])
    al = avg(losses[-n:])

    if al == 0:
        return 100.0

    return 100 - 100 / (1 + ag / al)


def macd(values):

    if len(values) < 35:
        return 0.0, 0.0, 0.0

    line = []

    for i in range(26, len(values) + 1):

        e12 = ema(values[:i], 12)
        e26 = ema(values[:i], 26)

        line.append(e12 - e26)

    m = line[-1]
    signal = ema(line, 9)

    return m, signal, m - signal


def bollinger(values, n=20, k=2):

    if len(values) < n:
        return 0.0, 0.0, 0.0

    x = values[-n:]

    middle = avg(x)

    variance = avg([
        (z - middle) ** 2
        for z in x
    ])

    sd = variance ** 0.5

    lower = middle - k * sd
    upper = middle + k * sd

    return lower, middle, upper


def adx(high, low, close, n=14):

    if len(close) < n * 2 + 1:
        return 0.0, 0.0, 0.0

    tr = []
    plus = []
    minus = []

    for i in range(1, len(close)):

        true_range = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]

        p = (
            up
            if up > down and up > 0
            else 0
        )

        m = (
            down
            if down > up and down > 0
            else 0
        )

        tr.append(true_range)
        plus.append(p)
        minus.append(m)

    atr = avg(tr[-n:])
    p = avg(plus[-n:])
    m = avg(minus[-n:])

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
