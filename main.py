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
def ema(values, period):
    if not values:
        return 0.0
    if len(values) < period:
        return avg(values)

    k = 2 / (period + 1)
    result = avg(values[:period])

    for value in values[period:]:
        result = value * k + result * (1 - k)

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    gain = avg(gains[-period:])
    loss = avg(losses[-period:])

    if loss == 0:
        return 100.0

    return 100 - 100 / (1 + gain / loss)


def macd(values):
    if len(values) < 35:
        return 0, 0, 0

    values_macd = []

    for i in range(26, len(values) + 1):
        values_macd.append(
            ema(values[:i], 12)
            - ema(values[:i], 26)
        )

    main = values_macd[-1]
    signal = ema(values_macd, 9)

    return main, signal, main - signal


def bb(values, period=20, k=2):
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


def adx(highs, lows, closes, period=14):
    if len(closes) < period * 2 + 1:
        return 0, 0, 0

    tr = []
    plus = []
    minus = []

    for i in range(1, len(closes)):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
        )

        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]

        plus.append(
            up if up > down and up > 0 else 0
        )

        minus.append(
            down if down > up and down > 0 else 0
        )

    atr = avg(tr[-period:])
    p = avg(plus[-period:])
    m = avg(minus[-period:])

    if atr <= 0:
        return 0, 0, 0

    plus_di = 100 * p / atr
    minus_di = 100 * m / atr

    total = plus_di + minus_di

    dx = (
        100 * abs(plus_di - minus_di) / total
        if total
        else 0
    )

    return dx, plus_di, minus_di


def daily_trend(symbol):
    now = time.time()

    with DAILY_CACHE_LOCK:
        cached = DAILY_CACHE.get(symbol)

        if cached:
            ts, data = cached
            if now - ts < DAILY_CACHE_TTL:
                return data

    data = klines(symbol, "1d", 100)

    if len(data) < 92:
        result = {
            "ok": False,
            "d30": 0,
            "d90": 0
        }

        with DAILY_CACHE_LOCK:
            DAILY_CACHE[symbol] = (now, result)

        return result

    closed = data[:-1]

    try:
        closes = [float(x[4]) for x in closed]
    except (TypeError, ValueError):
        return {
            "ok": False,
            "d30": 0,
            "d90": 0
        }

    current = closes[-1]

    result = {
        "ok": True,
        "d30": pct(closes[-31], current),
        "d90": pct(closes[-91], current)
    }

    with DAILY_CACHE_LOCK:
        DAILY_CACHE[symbol] = (now, result)

    return result


def long_term_penalty(d30, d90):
    penalty = 0

    if d30 <= LT30_STRONG:
        penalty -= 8
    elif d30 <= LT30_MILD:
        penalty -= 4

    if d90 <= LT90_EXTREME:
        penalty -= 15
    elif d90 <= LT90_STRONG:
        penalty -= 10
    elif d90 <= LT90_MILD:
        penalty -= 5

    return penalty


def trade_confidence(trades, volume_ratio):
    if trades <= 0:
        return 0

    if (
        volume_ratio >= 2
        and trades < MIN_1M_TRADES
    ):
        return 0.25

    if trades < MIN_1M_TRADES:
        return 0.40

    return min(
        1.0,
        max(
            0.40,
            trades / TRADE_REFERENCE
        )
    )


def market_context():
    now = time.time()

    with MARKET_CACHE_LOCK:
        cached = MARKET_CACHE.get(MARKET_SYMBOL)

        if cached:
            ts, data = cached
            if now - ts < DAILY_CACHE_TTL:
                return data

    data = klines(
        MARKET_SYMBOL,
        "5m",
        20
    )

    if len(data) < 5:
        return {
            "ok": False,
            "momentum": 0,
            "state": "VERİ YOK"
        }

    try:
        closes = [
            float(x[4])
            for x in data[:-1]
        ]
    except (TypeError, ValueError):
        return {
            "ok": False,
            "momentum": 0,
            "state": "VERİ YOK"
        }

    if len(closes) < 4:
        return {
            "ok": False,
            "momentum": 0,
            "state": "VERİ YOK"
        }

    momentum = pct(
        closes[-4],
        closes[-1]
    )

    if abs(momentum) >= MARKET_MOVE * 2:
        state = "AŞIRI HAREKETLİ"
    elif abs(momentum) >= MARKET_MOVE:
        state = "HAREKETLİ"
    elif momentum > 0.5:
        state = "POZİTİF"
    elif momentum < -0.5:
        state = "NEGATİF"
    else:
        state = "NÖTR"

    result = {
        "ok": True,
        "momentum": momentum,
        "state": state
    }

    with MARKET_CACHE_LOCK:
        MARKET_CACHE[MARKET_SYMBOL] = (
            now,
            result
        )

    return result
class DB:

    def __init__(self, path):
        self.path = path
        self.lock = Lock()

        with sqlite3.connect(path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA busy_timeout=5000")

            db.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL DEFAULT 0,
                    score REAL DEFAULT 0,
                    level TEXT DEFAULT 'NONE',
                    stage TEXT DEFAULT 'NONE',
                    updated REAL DEFAULT 0,
                    streak INTEGER DEFAULT 0,
                    streak_at REAL DEFAULT 0,
                    trap INTEGER DEFAULT 0,
                    priority REAL DEFAULT 0
                )
            """)

            db.execute("""
                CREATE TABLE IF NOT EXISTS signals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    ts REAL,
                    price REAL,
                    score REAL,
                    setup REAL,
                    confirmation REAL,
                    penalty REAL,
                    status TEXT,
                    max_pct REAL DEFAULT 0,
                    min_pct REAL DEFAULT 0,
                    c1 REAL,
                    c3 REAL,
                    c5 REAL,
                    c15 REAL,
                    entry_quality REAL DEFAULT 0,
                    priority REAL DEFAULT 0,
                    d30 REAL DEFAULT 0,
                    d90 REAL DEFAULT 0,
                    trade_1m REAL DEFAULT 0,
                    trade_5m REAL DEFAULT 0,
                    market_momentum REAL DEFAULT 0,
                    trap INTEGER DEFAULT 0
                )
            """)

            self._migrate(db, "state", {
                "streak":
                    "ALTER TABLE state ADD COLUMN streak INTEGER DEFAULT 0",
                "streak_at":
                    "ALTER TABLE state ADD COLUMN streak_at REAL DEFAULT 0",
                "trap":
                    "ALTER TABLE state ADD COLUMN trap INTEGER DEFAULT 0",
                "priority":
                    "ALTER TABLE state ADD COLUMN priority REAL DEFAULT 0"
            })

            self._migrate(db, "signals", {
                "entry_quality":
                    "ALTER TABLE signals ADD COLUMN entry_quality REAL DEFAULT 0",
                "priority":
                    "ALTER TABLE signals ADD COLUMN priority REAL DEFAULT 0",
                "d30":
                    "ALTER TABLE signals ADD COLUMN d30 REAL DEFAULT 0",
                "d90":
                    "ALTER TABLE signals ADD COLUMN d90 REAL DEFAULT 0",
                "trade_1m":
                    "ALTER TABLE signals ADD COLUMN trade_1m REAL DEFAULT 0",
                "trade_5m":
                    "ALTER TABLE signals ADD COLUMN trade_5m REAL DEFAULT 0",
                "market_momentum":
                    "ALTER TABLE signals ADD COLUMN market_momentum REAL DEFAULT 0",
                "trap":
                    "ALTER TABLE signals ADD COLUMN trap INTEGER DEFAULT 0"
            })

    def _migrate(self, db, table, columns):
        existing = {
            row[1]
            for row in db.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }

        for name, sql in columns.items():
            if name not in existing:
                db.execute(sql)

    def get(self, symbol):
        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):
            db.execute(
                "PRAGMA busy_timeout=5000"
            )

            return db.execute("""
                SELECT sent, score, level, stage,
                       updated, streak, streak_at,
                       trap, priority
                FROM state
                WHERE symbol=?
            """, (symbol,)).fetchone()

    def put(
        self,
        symbol,
        score,
        level,
        stage,
        sent=None,
        streak=None,
        trap=None,
        priority=None
    ):
        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):
            db.execute(
                "PRAGMA busy_timeout=5000"
            )

            old = db.execute("""
                SELECT sent, streak, trap, priority
                FROM state
                WHERE symbol=?
            """, (symbol,)).fetchone()

            sent_time = (
                time.time()
                if sent is not None
                else old[0] if old else 0
            )

            old_streak = old[1] if old else 0
            old_trap = old[2] if old else 0
            old_priority = old[3] if old else 0

            db.execute("""
                INSERT INTO state(
                    symbol, sent, score, level, stage,
                    updated, streak, streak_at,
                    trap, priority
                )
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    level=excluded.level,
                    stage=excluded.stage,
                    updated=excluded.updated,
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    priority=excluded.priority
            """, (
                symbol,
                sent_time,
                score,
                level,
                stage,
                time.time(),
                old_streak if streak is None else streak,
                time.time(),
                old_trap if trap is None else int(trap),
                old_priority if priority is None else priority
            ))

    def update_streak(
        self,
        symbol,
        qualified,
        trap=False
    ):
        now = time.time()

        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):
            db.execute(
                "PRAGMA busy_timeout=5000"
            )

            row = db.execute("""
                SELECT streak, streak_at
                FROM state
                WHERE symbol=?
            """, (symbol,)).fetchone()

            old_streak = int(row[0] or 0) if row else 0
            old_time = float(row[1] or 0) if row else 0

            if not qualified:
                streak = 0
            elif (
                old_time
                and now - old_time <= STREAK_WINDOW
            ):
                streak = old_streak + 1
            else:
                streak = 1

            db.execute("""
                INSERT INTO state(
                    symbol, streak, streak_at,
                    trap, updated
                )
                VALUES(?,?,?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    updated=excluded.updated
            """, (
                symbol,
                streak,
                now,
                int(trap),
                now
            ))

            return streak

    def can_send(self, symbol, level):
        row = self.get(symbol)

        if not row:
            return True

        sent = float(row[0] or 0)
        old_level = row[2]

        rank = {
            "BUY": 1,
            "VERY": 2
        }

        return (
            time.time() - sent >= COOLDOWN
            or
            rank.get(level, 0)
            > rank.get(old_level, 0)
        )

    def create_signal(self, r):
        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):
            db.execute(
                "PRAGMA busy_timeout=5000"
            )

            cur = db.execute("""
                INSERT INTO signals(
                    symbol, ts, price, score,
                    setup, confirmation, penalty,
                    status, entry_quality,
                    priority, d30, d90,
                    trade_1m, trade_5m,
                    market_momentum, trap
                )
                VALUES(
                    ?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?
                )
            """, (
                r["symbol"],
                time.time(),
                r["price"],
                r["score"],
                r["setup"],
                r["confirmation"],
                r["penalty"],
                r["status"],
                r.get("entry_quality", 0),
                r.get("priority", 0),
                r.get("d30", 0),
                r.get("d90", 0),
                r.get("trades_1m", 0),
                r.get("trades_5m", 0),
                r.get("market_momentum", 0),
                int(r.get("trap", False))
            ))

            return cur.lastrowid

    def update_outcomes(self, price_map):
        now = time.time()

        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):
            db.execute(
                "PRAGMA busy_timeout=5000"
            )

            rows = db.execute("""
                SELECT id, symbol, ts, price,
                       max_pct, min_pct,
                       c1, c3, c5, c15
                FROM signals
                WHERE ts > ?
            """, (
                now - OUTCOME_WINDOW,
            )).fetchall()

            for row in rows:
                (
                    sid, symbol, ts, price,
                    max_pct, min_pct,
                    c1, c3, c5, c15
                ) = row

                current = price_map.get(symbol)

                if (
                    not current
                    or not price
                    or price <= 0
                ):
                    continue

                change = (
                    (current - price)
                    / price
                    * 100
                )

                updates = {
                    "max_pct": max(max_pct, change),
                    "min_pct": min(min_pct, change)
                }

                elapsed = now - ts

                if elapsed >= 60 and c1 is None:
                    updates["c1"] = change

                if elapsed >= 180 and c3 is None:
                    updates["c3"] = change

                if elapsed >= 300 and c5 is None:
                    updates["c5"] = change

                if elapsed >= 900 and c15 is None:
                    updates["c15"] = change

                clause = ", ".join(
                    f"{k}=?"
                    for k in updates
                )

                db.execute(
                    f"UPDATE signals SET {clause} WHERE id=?",
                    (*updates.values(), sid)
                )

    def performance_summary(self):
        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):
            return db.execute("""
                SELECT
                    score, setup, confirmation,
                    max_pct, min_pct, c5, c15,
                    status, entry_quality, priority,
                    d30, d90, trade_1m, trade_5m,
                    market_momentum, trap
                FROM signals
                WHERE c15 IS NOT NULL
            """).fetchall()


DBS = DB(DB_PATH)


def candidates(data):
    result = []

    for ticker in data:
        symbol = ticker.get("symbol", "")

        if not symbol.endswith("TRY"):
            continue

        if symbol in EXCLUDED:
            continue

        try:
            volume = float(
                ticker.get("quoteVolume", 0)
            )

            change = float(
                ticker.get("priceChangePercent", 0)
            )

            price = float(
                ticker.get("lastPrice", 0)
            )

            if volume < MIN_QUOTE_VOLUME:
                continue

            if change > 25:
                continue

            result.append({
                "symbol": symbol,
                "volume": volume,
                "chg": change,
                "price": price
            })

        except (TypeError, ValueError):
            continue

    return result


def shortlist(items):
    def rank(item):
        return (
            item["volume"]
            *
            (
                1
                +
                max(item["chg"], 0) / 100
            )
        )

    return sorted(
        items,
        key=rank,
        reverse=True
    )[:SHORTLIST]
def analyze(item):

    symbol = item["symbol"]
    price = item["price"]

    try:

        k5 = klines(symbol, "5m", 80)

        if len(k5) < 40:
            return {"status": "PASS"}

        c5 = k5[:-1]

        close5 = [float(x[4]) for x in c5]
        volume5 = [float(x[7]) for x in c5]
        trades5 = [int(x[8]) for x in c5]

        avg5 = avg(volume5[-12:])
        recent5 = avg(volume5[-3:])

        vr5 = (
            recent5 / avg5
            if avg5
            else 0
        )

        momentum15 = pct(
            close5[-4],
            price
        )

        if (
            momentum15 < -3
            and vr5 < 1.3
        ):
            return {"status": "PASS"}

        trend = daily_trend(symbol)

        d30 = trend.get("d30", 0)
        d90 = trend.get("d90", 0)

        lt_penalty = (
            long_term_penalty(d30, d90)
            if trend.get("ok")
            else 0
        )

        k1 = klines(symbol, "1m", 180)

        if len(k1) < 100:
            return {"status": "PASS"}

        c1 = k1[:-1]

        close = [float(x[4]) for x in c1]
        high = [float(x[2]) for x in c1]
        low = [float(x[3]) for x in c1]
        volume = [float(x[7]) for x in c1]
        trades = [int(x[8]) for x in c1]

        if price <= 0:
            price = close[-1]

        momentum1 = pct(close[-2], price)
        momentum5 = pct(close5[-2], price)

        low90 = min(low[-90:])
        high90 = max(high[-90:])

        location = (
            (price - low90)
            /
            (high90 - low90)
            *
            100
            if high90 > low90
            else 50
        )

        avg_volume = avg(volume[-30:])
        last3 = avg(volume[-3:])
        previous = avg(volume[-10:-3])

        vr = (
            last3 / avg_volume
            if avg_volume
            else 0
        )

        impulse = (
            last3 / previous
            if previous
            else 1
        )

        buy_volume = sum(
            float(x[10])
            for x in c1[-5:]
        )

        total_volume = sum(
            float(x[7])
            for x in c1[-5:]
        )

        bp = (
            buy_volume
            /
            total_volume
            *
            100
            if total_volume
            else 50
        )

        trades1 = sum(trades[-5:])
        trades5 = sum(trades5[-1:])

        ema9 = ema(close, 9)
        ema21 = ema(close, 21)
        ema50 = ema(close, 50)

        ema9_old = ema(close[:-3], 9)
        ema21_old = ema(close[:-3], 21)

        ema_up = (
            ema9 > ema21
            and
            ema9 > ema9_old
        )

        ema_cross = (
            ema9 > ema21
            and
            ema9_old <= ema21_old
        )

        rv = rsi(close)
        old_rsi = rsi(close[:-3])

        _, _, macd_now = macd(close)
        _, _, macd_old = macd(close[:-3])

        macd_up = macd_now > macd_old

        ad, plus_di, minus_di = adx(
            high,
            low,
            close
        )

        lower, middle, upper = bb(close)

        width = (
            (upper - lower)
            /
            middle
            *
            100
            if middle
            else 0
        )

        old_lower, old_middle, old_upper = bb(
            close[:-5]
        )

        old_width = (
            (old_upper - old_lower)
            /
            old_middle
            *
            100
            if old_middle
            else width
        )

        squeeze = (
            width <= 2.2
            or
            (
                old_width > 0
                and width < old_width * 0.80
            )
        )

        expanding = (
            old_width > 0
            and width > old_width * 1.08
        )

        resistance = max(high[-30:-2])

        dist = max(
            0,
            (resistance - price)
            /
            price
            *
            100
        )

        breakout = price > resistance
        closed_breakout = close[-1] > resistance

        near = dist <= 0.35

        candle_range = high[-1] - low[-1]

        close_position = (
            (close[-1] - low[-1])
            /
            candle_range
            *
            100
            if candle_range > 0
            else 50
        )

        higher_low = (
            low[-1] > low[-3]
            and
            low[-3] >= low[-6]
        )

        trap_reasons = []

        if (
            bp < TRAP_BUYER
            and
            vr >= TRAP_VOLUME
        ):
            trap_reasons.append(
                "zayıf alıcı"
            )

        if (
            momentum5 < TRAP_MOMENTUM
            and
            not higher_low
        ):
            trap_reasons.append(
                "negatif momentum"
            )

        if (
            trades1 < MIN_1M_TRADES
            and
            vr >= 2
        ):
            trap_reasons.append(
                "düşük işlem"
            )

        trap = bool(trap_reasons)

        setup = 0

        if ema_up:
            setup += 12

        if ema_cross:
            setup += 6

        if squeeze:
            setup += 8

        if higher_low:
            setup += 6

        if (
            35 <= rv <= 65
            and rv > old_rsi
        ):
            setup += 8

        if price >= ema50:
            setup += 5

        if (
            near
            or dist <= 0.70
        ):
            setup += 8

        if vr >= 1.5:
            setup += 8

        if bp >= 58:
            setup += 5

        if trades1 >= MIN_1M_TRADES:
            setup += 3

        confirmation = 0

        if closed_breakout:
            confirmation += 18
        elif breakout:
            confirmation += 14

        if vr >= 2:
            confirmation += 12
        elif vr >= 1.5:
            confirmation += 7

        if vr5 >= 1.5:
            confirmation += 8

        if bp >= 65:
            confirmation += 7

        if macd_up:
            confirmation += 6

        if (
            plus_di > minus_di
            and ad >= 18
        ):
            confirmation += 7

        if close_position >= 65:
            confirmation += 4

        if expanding:
            confirmation += 4

        if (
            trades1 >= MIN_1M_TRADES
            and trades5 >= MIN_5M_TRADES
        ):
            confirmation += 3

        penalty = 0

        if momentum1 > 2.5:
            penalty -= 10

        if momentum5 > 5:
            penalty -= 12

        if rv > 78:
            penalty -= 10

        if (
            bp < 50
            and vr >= 1.8
        ):
            penalty -= 8

        if (
            momentum5 < -1.2
            and not higher_low
        ):
            penalty -= 12

        penalty += lt_penalty

        if (
            vr >= 2
            and trades1 < MIN_1M_TRADES
        ):
            penalty -= 8
        elif (
            vr >= 1.5
            and trades1 < MIN_1M_TRADES
        ):
            penalty -= 4

        if trap:
            penalty -= 12

        market = market_context()

        market_momentum = market.get(
            "momentum",
            0
        )

        if abs(market_momentum) >= MARKET_MOVE * 2:
            penalty -= 8
        elif abs(market_momentum) >= MARKET_MOVE:
            penalty -= 4

        entry = 100

        if rv >= 88:
            entry -= 30
        elif rv >= 78:
            entry -= 15

        if momentum1 >= 5:
            entry -= 25
        elif momentum1 >= 2.5:
            entry -= 12

        if momentum5 >= 5:
            entry -= 20
        elif momentum5 >= 3:
            entry -= 10

        if dist <= 0.15:
            entry -= 8
        elif dist <= 0.35:
            entry -= 4

        if closed_breakout:
            entry += 5

        if higher_low:
            entry += 5

        if trades1 >= MIN_1M_TRADES:
            entry += 4
        else:
            entry -= 8

        if trap:
            entry -= 20

        entry = max(
            0,
            min(100, int(round(entry)))
        )

        score = clamp(
            setup
            +
            confirmation
            +
            penalty
        )

        stage = "NONE"

        if setup >= 25:
            stage = "SETUP"

        if (
            score >= 68
            and confirmation >= 18
        ):
            stage = "CONFIRMED"

        very_ok = (
            d30 > LT30_STRONG
            and
            d90 > LT90_STRONG
        )

        if (
            score >= 84
            and confirmation >= 28
            and vr >= 1.5
            and very_ok
            and not trap
            and trades1 >= MIN_1M_TRADES
        ):
            stage = "VERY"

        level = {
            "VERY": "VERY",
            "CONFIRMED": "BUY",
            "SETUP": "INTERNAL"
        }.get(stage, "PASS")

        qualified = (
            stage in (
                "SETUP",
                "CONFIRMED",
                "VERY"
            )
            and not trap
        )

        streak = DBS.update_streak(
            symbol,
            qualified,
            trap
        )

        if level == "BUY" and streak < BUY_STREAK:
            level = "INTERNAL"

        if level == "VERY" and streak < VERY_STREAK:
            instant = (
                closed_breakout
                and vr >= 2
                and bp >= 65
                and trades1 >= MIN_1M_TRADES
                and not trap
            )

            if not instant:
                level = "INTERNAL"

        return {
            "status": level,
            "symbol": symbol,
            "score": score,
            "setup": setup,
            "confirmation": confirmation,
            "penalty": penalty,
            "price": price,
            "chg": item["chg"],
            "loc": location,
            "bp": bp,
            "vr": vr,
            "vr5": vr5,
            "impulse": impulse,
            "rv": rv,
            "ad": ad,
            "dist": dist,
            "ema": ema_up,
            "macd": macd_up,
            "squeeze": squeeze,
            "hl": higher_low,
            "breakout": breakout,
            "closed_breakout": closed_breakout,
            "trades_1m": trades1,
            "trades_5m": trades5,
            "trade_conf": trade_confidence(
                trades1,
                vr
            ),
            "d30": d30,
            "d90": d90,
            "trend_state": (
                "POZİTİF TREND"
                if d30 > 10 and d90 > 0
                else
                "YÜKSEK DÜŞÜŞ RİSKİ"
                if (
                    d90 <= LT90_EXTREME
                    or d30 <= LT30_STRONG
                )
                else
                "DÜŞÜŞ RİSKİ"
                if (
                    d90 <= LT90_STRONG
                    or d30 <= LT30_MILD
                )
                else "NÖTR"
            ),
            "trap": trap,
            "trap_reasons": trap_reasons,
            "entry_quality": entry,
            "streak": streak,
            "market_momentum": market_momentum,
            "market_state": market.get(
                "state",
                "VERİ YOK"
            )
        }

    except Exception as e:

        log.debug(
            "%s: %s",
            symbol,
            e
        )

        return {
            "status": "error",
            "symbol": symbol
        }
def priority_score(r):

    value = (
        r["score"] * 0.50
        +
        r["entry_quality"] * 0.25
        +
        r["trade_conf"] * 100 * 0.10
    )

    if r["streak"] >= 3:
        value += 8
    elif r["streak"] >= 2:
        value += 4

    if r["closed_breakout"]:
        value += 8
    elif r["breakout"]:
        value += 4

    if r["bp"] >= 75:
        value += 5
    elif r["bp"] >= 65:
        value += 3

    if r["vr"] >= 3:
        value += 5
    elif r["vr"] >= 2:
        value += 3
    elif r["vr"] >= 1.5:
        value += 1

    if r["vr5"] >= 2:
        value += 4
    elif r["vr5"] >= 1.5:
        value += 2

    if r["d90"] <= LT90_EXTREME:
        value -= 12
    elif r["d90"] <= LT90_STRONG:
        value -= 8
    elif r["d90"] <= LT90_MILD:
        value -= 4

    if r["d30"] <= LT30_STRONG:
        value -= 6
    elif r["d30"] <= LT30_MILD:
        value -= 3

    if r["trap"]:
        value -= 25

    return max(
        0,
        min(100, round(value, 1))
    )


def rank_signals(signals):

    for r in signals:
        r["priority"] = priority_score(r)

    signals.sort(
        key=lambda x: (
            x["priority"],
            x["entry_quality"],
            x["score"]
        ),
        reverse=True
    )

    for i, r in enumerate(
        signals,
        1
    ):
        r["rank"] = i

    return signals


def message(r):

    title = (
        "🔥 ÇOK GÜÇLÜ AL"
        if r["status"] == "VERY"
        else
        "🟢 AL"
    )

    reasons = []

    if r["closed_breakout"]:
        reasons.append(
            "Kapanış kırılımı"
        )
    elif r["breakout"]:
        reasons.append(
            "Direnç kırıldı"
        )
    elif r["dist"] <= 0.35:
        reasons.append(
            f"Direnç %{r['dist']:.2f}"
        )

    if r["vr"] >= 1.5:
        reasons.append(
            f"1m hacim {r['vr']:.1f}x"
        )

    if r["vr5"] >= 1.5:
        reasons.append(
            f"5m hacim {r['vr5']:.1f}x"
        )

    if r["impulse"] >= 2:
        reasons.append(
            f"İvme {r['impulse']:.1f}x"
        )

    if r["bp"] >= 65:
        reasons.append(
            f"Alıcı %{r['bp']:.0f}"
        )

    if r["ema"]:
        reasons.append("EMA trend")

    if r["macd"]:
        reasons.append(
            "MACD güçleniyor"
        )

    if r["hl"]:
        reasons.append("Higher-Low")

    if r["squeeze"]:
        reasons.append("BB sıkışma")

    if r["trades_1m"] >= MIN_1M_TRADES:
        reasons.append(
            "İşlem katılımı güçlü"
        )

    trap = ""

    if r["trap"]:
        trap = (
            "\n⚠️ TUZAK: "
            +
            ", ".join(
                r["trap_reasons"]
            )
            +
            "\n"
        )

    return (
        "🐋 BALİNA RADARI V22\n\n"
        f"{title}\n\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r['price']:.8g}\n"
        f"💪 Güç: {r['score']}/100\n"
        f"🏆 Öncelik: {r['priority']:.0f}/100\n"
        f"🎯 Giriş: {r['entry_quality']}/100\n"
        f"🔁 Teyit: {r['streak']}x\n\n"
        f"📊 1m Hacim: {r['vr']:.2f}x | "
        f"5m: {r['vr5']:.2f}x\n"
        f"🚀 İvme: {r['impulse']:.2f}x\n"
        f"🛒 Alıcı: %{r['bp']:.0f}\n"
        f"🔢 İşlem: {r['trades_1m']}\n"
        f"📈 RSI: {r['rv']:.0f} | "
        f"ADX: {r['ad']:.0f}\n"
        f"🎯 Direnç: %{r['dist']:.2f}\n"
        f"🚀 Kırılım: "
        f"{'✅' if r['breakout'] else '⏳'}\n"
        f"📅 30g: {r['d30']:+.1f}% | "
        f"90g: {r['d90']:+.1f}%\n"
        f"🌐 BTC/TRY: "
        f"{r['market_momentum']:+.2f}%\n"
        f"{trap}\n"
        f"🔎 {' • '.join(reasons[:8])}\n\n"
        +
        (
            "🚀 Güçlü teyit."
            if r["status"] == "VERY"
            else
            "🎯 Alım teyidi oluştu."
        )
    )


def scan():

    start = time.time()

    data = tickers()

    if not data:
        return True

    price_map = {}

    for item in data:
        try:
            price_map[
                item.get("symbol")
            ] = float(
                item.get(
                    "lastPrice",
                    0
                )
            )
        except (TypeError, ValueError):
            continue

    DBS.update_outcomes(
        price_map
    )

    all_candidates = candidates(data)
    items = shortlist(all_candidates)

    signals = []
    stats = {}

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as executor:

        jobs = [
            executor.submit(
                analyze,
                item
            )
            for item in items
        ]

        for job in as_completed(jobs):

            try:
                r = job.result()
            except Exception:
                r = {"status": "error"}

            status = r.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0)
                + 1
            )

            if status in (
                "BUY",
                "VERY"
            ):
                signals.append(r)

    signals = rank_signals(
        signals
    )

    sent = 0

    for r in signals:

        if sent >= MAX_SIGNALS:
            break

        if r["priority"] < MIN_PRIORITY:
            continue

        if not DBS.can_send(
            r["symbol"],
            r["status"]
        ):
            continue

        if telegram(
            message(r)
        ):

            DBS.put(
                r["symbol"],
                r["score"],
                r["status"],
                r["status"],
                sent=time.time(),
                streak=r["streak"],
                trap=r["trap"],
                priority=r["priority"]
            )

            DBS.create_signal(r)

            sent += 1

        time.sleep(0.3)

    elapsed = (
        time.time() - start
    )

    errors = stats.get(
        "error",
        0
    )

    log.info(
        "V22 | TRY:%d/%d | AL:%d | "
        "VERY:%d | Hata:%d | "
        "Gönder:%d | %.1fs",
        len(items),
        len(all_candidates),
        stats.get("BUY", 0),
        stats.get("VERY", 0),
        errors,
        sent,
        elapsed
    )

    return (
        errors / max(1, len(items)) > 0.30
        or
        elapsed > SCAN_INTERVAL * 1.25
    )
def performance():

    rows = DBS.performance_summary()

    if not rows:
        return {
            "samples": 0,
            "note": "Henüz 15dk+ tamamlanmış sinyal yok."
        }

    completed = [
        r for r in rows
        if r[6] is not None
    ]

    def stats(data):

        done = [
            r for r in data
            if r[6] is not None
        ]

        if not done:
            return {
                "samples": len(data),
                "completed": 0
            }

        return {
            "samples": len(data),
            "completed": len(done),
            "avg_15m_pct": round(
                avg([
                    r[6]
                    for r in done
                ]),
                2
            ),
            "positive_15m_pct": round(
                (
                    sum(
                        r[6] > 0
                        for r in done
                    )
                    /
                    len(done)
                    *
                    100
                ),
                1
            )
        }

    result = {
        "samples": len(rows),
        "completed_15m": len(completed),
        "avg_max_pct": round(
            avg([
                r[3]
                for r in rows
            ]),
            2
        ),
        "avg_min_pct": round(
            avg([
                r[4]
                for r in rows
            ]),
            2
        ),
        "avg_15m_pct": round(
            avg([
                r[6]
                for r in completed
            ]),
            2
        ) if completed else 0
    }

    result["score"] = {
        "68_75": stats([
            r for r in rows
            if 68 <= r[0] < 76
        ]),
        "76_83": stats([
            r for r in rows
            if 76 <= r[0] < 84
        ]),
        "84_90": stats([
            r for r in rows
            if 84 <= r[0] < 91
        ]),
        "91_100": stats([
            r for r in rows
            if r[0] >= 91
        ])
    }

    result["level"] = {
        "BUY": stats([
            r for r in rows
            if r[7] == "BUY"
        ]),
        "VERY": stats([
            r for r in rows
            if r[7] == "VERY"
        ])
    }

    result["entry_quality"] = {
        "0_49": stats([
            r for r in rows
            if r[8] < 50
        ]),
        "50_69": stats([
            r for r in rows
            if 50 <= r[8] < 70
        ]),
        "70_84": stats([
            r for r in rows
            if 70 <= r[8] < 85
        ]),
        "85_100": stats([
            r for r in rows
            if r[8] >= 85
        ])
    }

    return result


app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V22 Aktif"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V22",
        "base": BASE,
        "scan_interval": SCAN_INTERVAL,
        "workers": WORKERS
    }


@app.route("/performance")
def performance_route():
    return performance()


def validate_market():

    info = exchange_info()

    symbols = {
        x.get("symbol")
        for x in info.get(
            "symbols",
            []
        )
    }

    try_count = sum(
        s.endswith("TRY")
        for s in symbols
        if s
    )

    if try_count <= 0:
        raise RuntimeError(
            f"BASE {BASE} üzerinde TRY marketi bulunamadı."
        )

    if MARKET_SYMBOL not in symbols:
        log.warning(
            "%s bulunamadı; BTC filtresi devre dışı.",
            MARKET_SYMBOL
        )

    log.info(
        "V22 | Binance TR doğrulandı | TRY:%d",
        try_count
    )


def loop():

    log.info(
        "🐋 BALİNA RADARI V22 başlatılıyor..."
    )

    try:
        validate_market()
    except Exception as e:
        log.exception(
            "MARKET DOĞRULAMA HATASI: %s",
            e
        )
        return

    if TOKEN and CHAT:
        telegram(
            "🐋 BALİNA RADARI V22 AKTİF\n"
            "🏆 Öncelik sistemi aktif\n"
            "⚠️ TRAP filtresi aktif"
        )

    while True:

        started = time.time()

        try:
            backoff = scan()
        except Exception:
            log.exception(
                "Tarama döngüsü hatası"
            )
            backoff = True

        elapsed = (
            time.time() - started
        )

        if backoff:
            time.sleep(
                max(
                    180,
                    SCAN_INTERVAL * 3
                )
            )
        else:
            time.sleep(
                max(
                    1,
                    SCAN_INTERVAL - elapsed
                )
            )


Thread(
    target=loop,
    daemon=True,
    name="balina-v22"
).start()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),
        use_reloader=False
            )
