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


# =========================
# AYARLAR
# =========================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

# Bir taramada Telegram'a gönderilecek maksimum gerçek sinyal
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))

# Aynı seviyedeki tekrar sinyali engelle
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "900"))

TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v19.db")

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
    stream=sys.stdout
)

log = logging.getLogger("balina-v19")


# =========================
# HTTP
# =========================

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
        r = Retry(
            allowed_methods=["GET", "POST"],
            **kw
        )
    except TypeError:
        r = Retry(
            method_whitelist=["GET", "POST"],
            **kw
        )

    s = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20,
        max_retries=r
    )

    s.mount("https://", adapter)

    s.headers.update({
        "User-Agent": "BalinaRadari-V19/1.0"
    })

    return s


S = build_session()


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


def tickers(base):
    try:
        path = (
            "/api/v3/ticker/24hr"
            if base == SPOT
            else "/fapi/v1/ticker/24hr"
        )

        return api(base, path)

    except Exception as e:
        log.error("Ticker: %s", e)
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
        log.debug("%s %s: %s", symbol, interval, e)
        return []


def oi(symbol):
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


# =========================
# MATEMATİK / İNDİKATÖRLER
# =========================

def pct(a, b):
    if a and a > 0 and b is not None:
        return (b - a) / a * 100

    return 0.0


def avg(v):
    return sum(v) / len(v) if v else 0.0


def clamp(x):
    return max(0, min(100, int(round(x))))


def ema(v, n):
    if len(v) < n:
        return avg(v)

    k = 2 / (n + 1)

    e = avg(v[:n])

    for x in v[n:]:
        e = x * k + e * (1 - k)

    return e


def rsi(v, n=14):
    if len(v) < n + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(v)):
        d = v[i] - v[i - 1]

        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    ag = avg(gains[-n:])
    al = avg(losses[-n:])

    if al == 0:
        return 100.0

    return 100 - 100 / (1 + ag / al)


def macd(v):
    if len(v) < 40:
        return 0.0, 0.0, 0.0

    vals = []

    for i in range(26, len(v) + 1):
        fast = ema(v[:i], 12)
        slow = ema(v[:i], 26)

        vals.append(fast - slow)

    m = vals[-1]
    sig = ema(vals, 9)

    return m, sig, m - sig


def bb(v, n=20, k=2):
    if len(v) < n:
        return 0.0, 0.0, 0.0

    x = v[-n:]

    m = avg(x)

    sd = (
        avg([(z - m) ** 2 for z in x])
    ) ** 0.5

    return (
        m - k * sd,
        m,
        m + k * sd
    )


def adx(h, l, c, n=14):
    if len(c) < n * 2 + 1:
        return 0.0, 0.0, 0.0

    tr = []
    plus = []
    minus = []

    for i in range(1, len(c)):

        tr.append(
            max(
                h[i] - l[i],
                abs(h[i] - c[i - 1]),
                abs(l[i] - c[i - 1])
            )
        )

        up = h[i] - h[i - 1]
        dn = l[i - 1] - l[i]

        plus.append(
            up if up > dn and up > 0 else 0
        )

        minus.append(
            dn if dn > up and dn > 0 else 0
        )

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
# =========================
# VERİTABANI
# =========================

class DB:

    def __init__(self, path):

        self.path = path
        self.lock = Lock()

        with sqlite3.connect(path) as d:

            d.execute("""
                CREATE TABLE IF NOT EXISTS signals(
                    symbol TEXT PRIMARY KEY,
                    sent REAL,
                    score REAL,
                    level TEXT
                )
            """)

            d.execute("""
                CREATE TABLE IF NOT EXISTS oi(
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    ts REAL
                )
            """)


    def previous(self, symbol):

        with self.lock, sqlite3.connect(self.path) as d:

            return d.execute(
                """
                SELECT score, level
                FROM signals
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()


    def last_sent(self, symbol):

        with self.lock, sqlite3.connect(self.path) as d:

            r = d.execute(
                """
                SELECT sent
                FROM signals
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()

        return r[0] if r else 0


    def can_send(self, symbol, level):

        r = self.previous(symbol)

        if not r:
            return True

        old_level = r[1]

        rank = {
            "AL": 1,
            "VERY": 2
        }

        old_rank = rank.get(old_level, 0)
        new_rank = rank.get(level, 0)

        # Daha güçlü seviyeye geçildiyse
        # cooldown bekleme
        if new_rank > old_rank:
            return True

        # Aynı seviyeyi tekrar tekrar gönderme
        return (
            time.time() -
            self.last_sent(symbol)
        ) >= COOLDOWN


    def sent(self, symbol, score, level):

        with self.lock, sqlite3.connect(self.path) as d:

            d.execute(
                """
                INSERT INTO signals
                VALUES(?,?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    level=excluded.level
                """,
                (
                    symbol,
                    time.time(),
                    score,
                    level
                )
            )


    def getoi(self, symbol):

        with self.lock, sqlite3.connect(self.path) as d:

            r = d.execute(
                """
                SELECT value, ts
                FROM oi
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()

        if not r:
            return None

        if time.time() - r[1] > SCAN_INTERVAL * 5:
            return None

        return float(r[0])


    def putoi(self, symbol, value):

        if value is None:
            return

        with self.lock, sqlite3.connect(self.path) as d:

            d.execute(
                """
                INSERT INTO oi
                VALUES(?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
                """,
                (
                    symbol,
                    value,
                    time.time()
                )
            )


DBS = DB(DB_PATH)


# =========================
# ARA SİNYAL HAFIZASI
# =========================

# Telegram'a gönderilmeyen erken aşamaları
# bot kendi içinde takip eder.
MEMORY = {}
MEMORY_LOCK = Lock()


def remember(symbol, score):

    with MEMORY_LOCK:

        old = MEMORY.get(symbol)

        MEMORY[symbol] = {
            "score": score,
            "ts": time.time()
        }

    return old


# =========================
# ADAYLAR
# =========================

def candidates(st, ft):

    futures = {
        x.get("symbol"): x
        for x in ft
    }

    out = []

    for x in st:

        symbol = x.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        if symbol in EXCLUDED:
            continue

        if any(
            symbol.endswith(z)
            for z in (
                "UPUSDT",
                "DOWNUSDT",
                "BULLUSDT",
                "BEARUSDT"
            )
        ):
            continue

        f = futures.get(symbol)

        if not f:
            continue

        try:

            spot_volume = float(
                x.get("quoteVolume", 0)
            )

            futures_volume = float(
                f.get("quoteVolume", 0)
            )

            change = float(
                x.get("priceChangePercent", 0)
            )

            if (
                spot_volume < MIN_VOLUME
                or futures_volume < MIN_VOLUME
            ):
                continue

            # Aşırı dikleşmiş coinleri
            # ilk aşamada filtrele.
            if change > 25:
                continue

            out.append(symbol)

        except (TypeError, ValueError):
            continue

    return out
