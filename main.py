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
# 🐋 BALİNA RADARI V15 — AYARLAR VE ALT YAPI
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

STRONG_THRESHOLD = int(os.getenv("STRONG_THRESHOLD", "82"))
CANDIDATE_THRESHOLD = int(os.getenv("CANDIDATE_THRESHOLD", "72"))

MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v15.db")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {
    "BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT",
    "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BUSDUSDT"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("balina-v15")

def build_session():
    kw = dict(
        total=2, connect=2, read=2, backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False
    )
    try:
        r = Retry(allowed_methods=["GET", "POST"], **kw)
    except TypeError:
        r = Retry(method_whitelist=["GET", "POST"], **kw)

    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=r)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "BalinaRadari-V15/1.0"})
    return s

S = build_session()

def api(base, path, params=None):
    r = S.get(base + path, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def telegram(text):
    if not TOKEN or not CHAT:
        log.warning("Telegram ayarları eksik.")
        return False
    try:
        r = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT, "text": text},
            timeout=TIMEOUT
        )
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        log.error("Telegram hatası: %s", e)
        return False

def tickers(base):
    try:
        path = "/api/v3/ticker/24hr" if base == SPOT else "/fapi/v1/ticker/24hr"
        return api(base, path)
    except Exception as e:
        log.error("Ticker hatası: %s", e)
        return []

def klines(base, symbol, interval, limit):
    try:
        path = "/api/v3/klines" if base == SPOT else "/fapi/v1/klines"
        return api(base, path, {"symbol": symbol, "interval": interval, "limit": limit})
    except Exception as e:
        log.debug("%s %s kline hatası: %s", symbol, interval, e)
        return []

def open_interest(symbol):
    try:
        data = api(FUT, "/fapi/v1/openInterest", {"symbol": symbol})
        return float(data["openInterest"])
    except Exception:
        return None

def pct(a, b):
    return ((b - a) / a * 100.0) if a and a > 0 and b is not None else 0.0

def clamp(x):
    return max(0, min(100, int(round(x))))

def average(values):
    return sum(values) / len(values) if values else 0.0

class DB:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        with self.lock, sqlite3.connect(path) as d:
            d.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY, sent REAL, score REAL)")
            d.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY, value REAL, ts REAL)")

    def get_oi(self, s):
        with self.lock, sqlite3.connect(self.path) as d:
            r = d.execute("SELECT value, ts FROM oi WHERE symbol=?", (s,)).fetchone()
        if not r or (time.time() - r[1] > SCAN_INTERVAL * 5): return None
        return float(r[0])

    def put_oi(self, s, v):
        if v is None: return
        with self.lock, sqlite3.connect(self.path) as d:
            d.execute("INSERT INTO oi VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, ts=excluded.ts", (s, v, time.time()))

    def cooldown(self, s):
        with self.lock, sqlite3.connect(self.path) as d:
            r = d.execute("SELECT sent FROM state WHERE symbol=?", (s,)).fetchone()
        return bool(r and (time.time() - r[0] < COOLDOWN))

    def sent(self, s, score):
        with self.lock, sqlite3.connect(self.path) as d:
            d.execute("INSERT INTO state VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent, score=excluded.score", (s, time.time(), score))

DBS = DB(DB_PATH)

def candidates(st, ft):
    fm = {x.get("symbol"): x for x in ft}
    out = []
    for x in st:
        s = x.get("symbol", "")
        if not s.endswith("USDT") or s in EXCLUDED or any(s.endswith(z) for z in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        f = fm.get(s)
        if not f: continue
        try:
            spot_vol = float(x.get("quoteVolume", 0))
            fut_vol = float(f.get("quoteVolume", 0))
            daily_change = float(x.get("priceChangePercent", 0))

            if spot_vol < MIN_VOLUME or fut_vol < MIN_VOLUME: continue
            if daily_change > 16.0: continue

            out.append(s)
        except (TypeError, ValueError):
            continue
    return out
