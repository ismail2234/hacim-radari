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

# ============================================================
# 🐋 BALİNA RADARI V16.1 — SIGNAL PROGRESSION EDITION
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))
STRONG_THRESHOLD = int(os.getenv("STRONG_THRESHOLD", "82"))
ROCKET_THRESHOLD = int(os.getenv("ROCKET_THRESHOLD", "90"))
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v161.db")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {
    "BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT",
    "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BUSDUSDT"
}

# ============================================================
# LOGGING & SESSION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)

log = logging.getLogger("balina-v161")


def build_session():
    kw = dict(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )

    try:
        retry = Retry(
            allowed_methods=["GET", "POST"],
            **kw
        )
    except TypeError:
        retry = Retry(
            method_whitelist=["GET", "POST"],
            **kw
        )

    s = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20,
        max_retries=retry
    )

    s.mount("https://", adapter)
    s.mount("http://", adapter)

    s.headers.update({
        "User-Agent": "BalinaRadari-V16.1/1.0"
    })

    return s


S = build_session()


# ============================================================
# API & TELEGRAM
# ============================================================

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
        log.warning("Telegram ayarlari eksik.")
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
        log.error("Telegram hatasi: %s", e)
        return False


def tickers(base):
    try:
        path = (
            "/api/v3/ticker/24hr"
            if base == SPOT
            else "/fapi/v1/ticker/24hr"
        )

        return api(base, path)

    except Exception as e:
        log.error("Ticker hatasi: %s", e)
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
                "limit": limit
            }
        )

    except Exception as e:
        log.debug(
            "%s %s kline hatasi: %s",
            symbol,
            interval,
            e
        )
        return []


def open_interest(symbol):
    try:
        data = api(
            FUT,
            "/fapi/v1/openInterest",
            {"symbol": symbol}
        )

        return float(data["openInterest"])

    except Exception:
        return None


# ============================================================
# MATEMATİK
# ============================================================

def pct(a, b):
    if a is None or a <= 0 or b is None:
        return 0.0

    return (b - a) / a * 100.0


def clamp(x):
    return max(
        0,
        min(
            100,
            int(round(x))
        )
    )


def average(values):
    return sum(values) / len(values) if values else 0.0


# ============================================================
# DATABASE
# ============================================================

class DB:

    def __init__(self, path):
        self.path = path
        self.lock = Lock()

        with self.lock, sqlite3.connect(path) as d:

            d.execute(
                "CREATE TABLE IF NOT EXISTS state("
                "symbol TEXT PRIMARY KEY,"
                "sent REAL,"
                "score REAL)"
            )

            d.execute(
                "CREATE TABLE IF NOT EXISTS oi("
                "symbol TEXT PRIMARY KEY,"
                "value REAL,"
                "ts REAL)"
            )

            d.execute(
                "CREATE TABLE IF NOT EXISTS progression("
                "symbol TEXT PRIMARY KEY,"
                "stage INTEGER,"
                "stage_ts REAL,"
                "stage_score REAL)"
            )


    def get_oi(self, symbol):

        with self.lock, sqlite3.connect(self.path) as d:
            row = d.execute(
                "SELECT value,ts FROM oi WHERE symbol=?",
                (symbol,)
            ).fetchone()

        if not row:
            return None

        if time.time() - row[1] > SCAN_INTERVAL * 5:
            return None

        return float(row[0])


    def put_oi(self, symbol, value):

        if value is None:
            return

        with self.lock, sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO oi(symbol,value,ts) "
                "VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "value=excluded.value,"
                "ts=excluded.ts",
                (
                    symbol,
                    value,
                    time.time()
                )
            )


    def get_stage(self, symbol):

        with self.lock, sqlite3.connect(self.path) as d:
            row = d.execute(
                "SELECT stage,stage_ts,stage_score "
                "FROM progression "
                "WHERE symbol=?",
                (symbol,)
            ).fetchone()

        if not row:
            return 0, None, None

        if time.time() - row[1] > COOLDOWN:
            return 0, None, None

        return (
            int(row[0]),
            float(row[1]),
            float(row[2])
        )


    def set_stage(self, symbol, stage, score):

        with self.lock, sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO progression("
                "symbol,stage,stage_ts,stage_score"
                ") VALUES(?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "stage=excluded.stage,"
                "stage_ts=excluded.stage_ts,"
                "stage_score=excluded.stage_score",
                (
                    symbol,
                    stage,
                    time.time(),
                    score
                )
            )


    def sent(self, symbol, score):

        with self.lock, sqlite3.connect(self.path) as d:
            d.execute(
                "INSERT INTO state(symbol,sent,score) "
                "VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "sent=excluded.sent,"
                "score=excluded.score",
                (
                    symbol,
                    time.time(),
                    score
                )
            )


DBS = DB(DB_PATH)
