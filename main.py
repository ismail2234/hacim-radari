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
# 🐋 BALİNA RADARI V17 — STAGE TRACKER
# ============================================================

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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

log = logging.getLogger("balina-v17")


# ============================================================
# SESSION
# ============================================================

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


# ============================================================
# API
# ============================================================

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


# ============================================================
# HELPERS
# ============================================================

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


# ============================================================
# DATABASE
# ============================================================

class DB:

    def __init__(self, path):

        self.path = path
        self.lock = Lock()

        with self.lock, sqlite3.connect(path) as db:

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL,
                    score REAL,
                    stage INTEGER DEFAULT 0
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS oi(
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    ts REAL
                )
                """
            )

            columns = {
                row[1]
                for row in db.execute(
                    "PRAGMA table_info(state)"
                ).fetchall()
            }

            if "stage" not in columns:
                db.execute(
                    "ALTER TABLE state "
                    "ADD COLUMN stage INTEGER DEFAULT 0"
                )


    def get_stage(self, symbol):

        with self.lock, sqlite3.connect(self.path) as db:

            row = db.execute(
                "SELECT stage FROM state WHERE symbol=?",
                (symbol,),
            ).fetchone()

        return int(row[0]) if row else 0


    def can_send(self, symbol, stage):

        with self.lock, sqlite3.connect(self.path) as db:

            row = db.execute(
                "SELECT stage,sent FROM state WHERE symbol=?",
                (symbol,),
            ).fetchone()

        if not row:
            return True

        old_stage = int(row[0] or 0)
        sent_time = float(row[1] or 0)

        if stage > old_stage:
            return True

        return (
            stage == old_stage
            and time.time() - sent_time >= COOLDOWN
        )


    def sent(self, symbol, score, stage):

        with self.lock, sqlite3.connect(self.path) as db:

            db.execute(
                """
                INSERT INTO state(symbol,sent,score,stage)
                VALUES(?,?,?,?)

                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    stage=excluded.stage
                """,
                (
                    symbol,
                    time.time(),
                    score,
                    stage,
                ),
            )


    def get_oi(self, symbol):

        with self.lock, sqlite3.connect(self.path) as db:

            row = db.execute(
                "SELECT value,ts FROM oi WHERE symbol=?",
                (symbol,),
            ).fetchone()

        if not row:
            return None

        if time.time() - row[1] > SCAN_INTERVAL * 5:
            return None

        return float(row[0])


    def put_oi(self, symbol, value):

        if value is None:
            return

        with self.lock, sqlite3.connect(self.path) as db:

            db.execute(
                """
                INSERT INTO oi(symbol,value,ts)
                VALUES(?,?,?)

                ON CONFLICT(symbol)
                DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
                """,
                (
                    symbol,
                    value,
                    time.time(),
                ),
            )


DBS = DB(DB_PATH)


# ============================================================
# CANDIDATES
# ============================================================

def candidates(spot_tickers, futures_tickers):

    futures_map = {
        item.get("symbol"): item
        for item in futures_tickers
    }

    output = []

    for item in spot_tickers:

        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        if symbol in EXCLUDED:
            continue

        if any(
            symbol.endswith(x)
            for x in (
                "UPUSDT",
                "DOWNUSDT",
                "BULLUSDT",
                "BEARUSDT",
            )
        ):
            continue

        future = futures_map.get(symbol)

        if not future:
            continue

        try:

            spot_volume = float(
                item.get("quoteVolume", 0)
            )

            futures_volume = float(
                future.get("quoteVolume", 0)
            )

            daily_change = float(
                item.get("priceChangePercent", 0)
            )

            if spot_volume < MIN_VOLUME:
                continue

            if futures_volume < MIN_VOLUME:
                continue

            if daily_change > 16:
                continue

            output.append(symbol)

        except (TypeError, ValueError):
            continue

    return output


# ============================================================
# ANALYSIS
# ============================================================

def analyze(symbol):

    try:

        spot = klines(
            SPOT,
            symbol,
            "1m",
            48,
        )

        futures = klines(
            FUT,
            symbol,
            "1m",
            36,
        )

        spot5 = klines(
            SPOT,
            symbol,
            "5m",
            18,
        )

        if (
            len(spot) < 35
            or len(futures) < 30
            or len(spot5) < 10
        ):
            return {"status": "insufficient"}

        live = spot[-1]

        price = float(live[4])
        live_open = float(live[1])

        live_low = float(live[3])

        live_change = pct(
            live_open,
            price,
        )

        closes5 = [
            float(x[4])
            for x in spot5
        ]

        m5 = pct(
            closes5[-2],
            price,
        )

        m15 = pct(
            closes5[-4],
            price,
        )

        m30 = pct(
            closes5[-7],
            price,
        )

        # ----------------------------------------------------
        # LATE / WEAK FILTER
        # ----------------------------------------------------

        if (
            live_change > 1.2
            or m5 > 2.5
            or m15 > 4.5
            or m30 > 7
        ):
            return {"status": "late"}

        if (
            live_change < -2
            or m5 < -3.5
        ):
            return {"status": "weak"}

        # ----------------------------------------------------
        # MA SQUEEZE
        # ----------------------------------------------------

        closes = [
            float(x[4])
            for x in spot
        ]

        ma7 = avg(closes[-7:])
        ma30 = avg(closes[-30:])

        ma_difference = (
            abs(ma7 - ma30)
            / price
            * 100
        )

        ma_squeeze = ma_difference <= 0.85

        previous_ma = avg(
            closes[-10:-3]
        )

        ma_turning_up = ma7 > previous_ma

        # ----------------------------------------------------
        # LOCAL BOTTOM
        # ----------------------------------------------------

        closed = spot[:-1]

        lows = [
            float(x[3])
            for x in closed[-30:]
        ]

        highs = [
            float(x[2])
            for x in closed[-30:]
        ]

        low_price = min(lows)
        high_price = max(highs)

        location = (
            (price - low_price)
            / (high_price - low_price)
            * 100
            if high_price > low_price
            else 50
        )

        very_low = location <= 25
        near_low = location <= 40

        base_forming = (
            location <= 35
            and min(lows[-6:]) >= low_price
        )

        # ----------------------------------------------------
        # PRICE ACTION
        # ----------------------------------------------------

        a = spot[-2]
        b = spot[-3]

        a_open = float(a[1])
        a_high = float(a[2])
        a_low = float(a[3])
        a_close = float(a[4])

        b_low = float(b[3])

        higher_low = (
            a_low > b_low
            and live_low >= a_low
        )

        break_high = price > a_high

        body = abs(
            a_close - a_open
        )

        lower_wick = (
            min(a_open, a_close)
            - a_low
        )

        wick_rejection = (
            lower_wick > 0
            and lower_wick >= body * 0.8
        )

        reversal = (
            higher_low
            or break_high
            or wick_rejection
        )

        # ----------------------------------------------------
        # FLOW
        # ----------------------------------------------------

        closed_spot = spot[:-1]
        closed_futures = futures[:-1]

        spot_volume = [
            float(x[7])
            for x in spot
        ]

        futures_volume = [
            float(x[7])
            for x in futures
        ]

        trades = [
            float(x[8])
            for x in spot
        ]

        average_spot = avg([
            float(x[7])
            for x in closed_spot[-18:]
        ])

        average_futures = avg([
            float(x[7])
            for x in closed_futures[-18:]
        ])

        average_trades = avg([
            float(x[8])
            for x in closed_spot[-18:]
        ])

        if min(
            average_spot,
            average_futures,
            average_trades,
        ) <= 0:
            return {"status": "insufficient"}

        spot_ratio = (
            avg(spot_volume[-3:])
            / average_spot
        )

        futures_ratio = (
            avg(futures_volume[-3:])
            / average_futures
        )

        trade_ratio = (
            avg(trades[-3:])
            / average_trades
        )

        previous_volume = avg(
            spot_volume[-6:-3]
        )

        volume_acceleration = (
            avg(spot_volume[-3:])
            / previous_volume
            if previous_volume > 0
            else 1
        )

        silent_accumulation = (
            -0.35 <= live_change <= 0.45
            and (
                spot_ratio >= 2.2
                or futures_ratio >= 2.2
            )
        )

        # ----------------------------------------------------
        # BUY PRESSURE
        # ----------------------------------------------------

        buy3 = sum(
            float(x[10])
            for x in spot[-3:]
        )

        volume3 = sum(
            float(x[7])
            for x in spot[-3:]
        )

        buy5 = sum(
            float(x[10])
            for x in spot[-5:]
        )

        volume5 = sum(
            float(x[7])
            for x in spot[-5:]
        )

        bp3 = (
            buy3 / volume3 * 100
            if volume3
            else 50
        )

        bp5 = (
            buy5 / volume5 * 100
            if volume5
            else 50
        )

        buy_pressure = (
            bp3 * 0.65
            + bp5 * 0.35
        )

        spot_leads = (
            spot_ratio >= 2
            and spot_ratio >= futures_ratio * 1.15
        )

        # ----------------------------------------------------
        # VOLATILITY COMPRESSION
        # ----------------------------------------------------

        ranges = []

        for candle in closed[-12:]:

            low = float(candle[3])

            if low <= 0:
                continue

            high = float(candle[2])

            ranges.append(
                (high - low)
                / low
                * 100
            )

        volatility_compression = False

        if len(ranges) >= 8:

            recent = avg(
                ranges[-4:]
            )

            previous = avg(
                ranges[-8:-4]
            )

            if previous > 0:
                volatility_compression = (
                    recent
                    <= previous * 0.75
                )

        # ----------------------------------------------------
        # BREAKOUT DISTANCE
        # ----------------------------------------------------

        resistance = max(
            highs[-8:]
        )

        breakout_distance = (
            max(
                0,
                (resistance - price)
                / price
                * 100,
            )
            if resistance > 0
            else 99
        )

        near_breakout = (
            breakout_distance <= 0.60
        )

        # ----------------------------------------------------
        # RISK FILTERS
        # ----------------------------------------------------

        falling = (
            m5 < -0.8
            and m15 < -1.2
            and not reversal
        )

        distribution = (
            buy_pressure < 58
            and spot_ratio >= 3
            and m5 < -0.3
        )

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = 0
        reasons = []

        if very_low:
            score += 20
            reasons.append(
                "🟦 Çok güçlü yerel dip bölgesi"
            )

        elif near_low:
            score += 14
            reasons.append(
                "🟦 Yerel dip/birikim bölgesi"
            )

        if base_forming:
            score += 8
            reasons.append(
                "🧱 Dip tabanı oluşuyor"
            )

        if ma_squeeze:
            score += 12
            reasons.append(
                f"📐 MA sıkışması (%{ma_difference:.2f})"
            )

        if ma_turning_up:
            score += 5
            reasons.append(
                "📈 Kısa MA yukarı dönüyor"
            )

        if silent_accumulation:
            score += 12
            reasons.append(
                "🤫 Sessiz birikim tespit edildi"
            )

        if volatility_compression:
            score += 8
            reasons.append(
                "🤏 Volatilite daralıyor"
            )

        if spot_ratio >= 3.5:
            score += 12
            reasons.append(
                f"🐋 Spot akışı çok güçlü ({spot_ratio:.2f}x)"
            )

        elif spot_ratio >= 2:
            score += 8
            reasons.append(
                f"📈 Spot akışı başladı ({spot_ratio:.2f}x)"
            )

        if spot_leads:
            score += 5
            reasons.append(
                "🐋 Spot akışı futures'tan önce geliyor"
            )

        if buy_pressure >= 78:
            score += 12
            reasons.append(
                f"🟢 Çok güçlü alıcı baskısı (%{buy_pressure:.1f})"
            )

        elif buy_pressure >= 68:
            score += 8
            reasons.append(
                f"🟢 Güçlü alıcı baskısı (%{buy_pressure:.1f})"
            )

        elif buy_pressure >= 62:
            score += 5
            r
