
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
class DB:

    def __init__(self, path):

        self.path = path
        self.lock = Lock()

        with sqlite3.connect(path) as db:

            db.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL DEFAULT 0,
                    score REAL DEFAULT 0,
                    level TEXT DEFAULT 'NONE',
                    stage TEXT DEFAULT 'NONE',
                    updated REAL DEFAULT 0
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
                    c15 REAL
                )
            """)

    def get(self, symbol):

        with self.lock, sqlite3.connect(self.path) as db:
            return db.execute(
                """
                SELECT sent, score, level, stage, updated
                FROM state
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()

    def put(
        self,
        symbol,
        score,
        level,
        stage,
        sent=None
    ):

        with self.lock, sqlite3.connect(self.path) as db:

            old = db.execute(
                "SELECT sent FROM state WHERE symbol=?",
                (symbol,)
            ).fetchone()

            sent_time = (
                time.time()
                if sent is not None
                else old[0] if old else 0
            )

            db.execute(
                """
                INSERT INTO state(
                    symbol,
                    sent,
                    score,
                    level,
                    stage,
                    updated
                )
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    level=excluded.level,
                    stage=excluded.stage,
                    updated=excluded.updated
                """,
                (
                    symbol,
                    sent_time,
                    score,
                    level,
                    stage,
                    time.time()
                )
            )

    def can_send(self, symbol, level):

        row = self.get(symbol)

        if not row:
            return True

        previous_sent = float(row[0] or 0)
        previous_level = row[2]

        rank = {
            "BUY": 1,
            "VERY": 2
        }

        old_rank = rank.get(
            previous_level,
            0
        )

        new_rank = rank.get(
            level,
            0
        )

        return (
            time.time() - previous_sent >= COOLDOWN
            or
            new_rank > old_rank
        )

    def create_signal(
        self,
        symbol,
        price,
        score,
        setup,
        confirmation,
        penalty,
        status
    ):

        with self.lock, sqlite3.connect(self.path) as db:

            cur = db.execute(
                """
                INSERT INTO signals(
                    symbol,
                    ts,
                    price,
                    score,
                    setup,
                    confirmation,
                    penalty,
                    status
                )
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    symbol,
                    time.time(),
                    price,
                    score,
                    setup,
                    confirmation,
                    penalty,
                    status
                )
            )

            return cur.lastrowid

    def update_outcomes(self, price_map):

        now = time.time()

        with self.lock, sqlite3.connect(self.path) as db:

            rows = db.execute(
                """
                SELECT
                    id,
                    symbol,
                    ts,
                    price,
                    max_pct,
                    min_pct,
                    c1,
                    c3,
                    c5,
                    c15
                FROM signals
                WHERE c15 IS NULL
                """
            ).fetchall()

            for row in rows:

                (
                    signal_id,
                    symbol,
                    ts,
                    price,
                    max_pct,
                    min_pct,
                    c1,
                    c3,
                    c5,
                    c15
                ) = row

                current_price = price_map.get(symbol)

                if (
                    not current_price
                    or not price
                    or price <= 0
                ):
                    continue

                change = (
                    current_price - price
                ) / price * 100

                new_max = max(
                    float(max_pct or 0),
                    change
                )

                new_min = min(
                    float(min_pct or 0),
                    change
                )

                elapsed = now - ts

                updates = {
                    "max_pct": new_max,
                    "min_pct": new_min
                }

                if elapsed >= 60 and c1 is None:
                    updates["c1"] = change

                if elapsed >= 180 and c3 is None:
                    updates["c3"] = change

                if elapsed >= 300 and c5 is None:
                    updates["c5"] = change

                if elapsed >= 900 and c15 is None:
                    updates["c15"] = change

                if updates:

                    clause = ", ".join(
                        f"{key}=?"
                        for key in updates
                    )

                    db.execute(
                        f"""
                        UPDATE signals
                        SET {clause}
                        WHERE id=?
                        """,
                        (
                            *updates.values(),
                            signal_id
                        )
                    )

    def performance_summary(self):

        with self.lock, sqlite3.connect(self.path) as db:

            return db.execute(
                """
                SELECT
                    score,
                    setup,
                    confirmation,
                    max_pct,
                    min_pct,
                    c5,
                    c15
                FROM signals
                WHERE c15 IS NOT NULL
                """
            ).fetchall()


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

            quote_volume = float(
                ticker.get("quoteVolume", 0)
            )

            change = float(
                ticker.get("priceChangePercent", 0)
            )

            price = float(
                ticker.get("lastPrice", 0)
            )

            if (
                quote_volume < MIN_QUOTE_VOLUME
                or price <= 0
            ):
                continue

            result.append({
                "symbol": symbol,
                "volume": quote_volume,
                "chg": change,
                "price": price
            })

        except (TypeError, ValueError):

            continue

    return result


def shortlist(items):

    def ranking(item):

        volume = item["volume"]
        change = item["chg"]

        activity = (
            1
            +
            max(change, 0) / 100
        )

        return volume * activity

    return sorted(
        items,
        key=ranking,
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
        high5 = [float(x[2]) for x in c5]
        low5 = [float(x[3]) for x in c5]
        vol5 = [float(x[7]) for x in c5]

        avg5 = avg(vol5[-12:])
        recent5 = avg(vol5[-3:])

        vr5 = (
            recent5 / avg5
            if avg5 > 0 else 0
        )

        momentum15 = pct(
            close5[-4],
            price
        )

        if momentum15 < -3 and vr5 < 1.3:
            return {"status": "PASS"}


        k1 = klines(symbol, "1m", 180)

        if len(k1) < 100:
            return {"status": "PASS"}

        c1 = k1[:-1]

        close = [float(x[4]) for x in c1]
        high = [float(x[2]) for x in c1]
        low = [float(x[3]) for x in c1]
        volume = [float(x[7]) for x in c1]

        if price <= 0:
            price = close[-1]


        momentum1 = pct(
            close[-2],
            price
        )

        momentum5 = pct(
            close[-6],
            price
        )


        low90 = min(low[-90:])
        high90 = max(high[-90:])

        location = (
            (
                price - low90
            )
            /
            (high90 - low90)
            * 100
            if high90 > low90
            else 50
        )


        avg_volume = avg(volume[-30:])
        recent_volume = avg(volume[-3:])
        previous_volume = avg(volume[-10:-3])

        vr = (
            recent_volume / avg_volume
            if avg_volume > 0 else 0
        )

        impulse = (
            recent_volume / previous_volume
            if previous_volume > 0 else 1
        )


        buy_volume = sum(
            float(x[10])
            for x in c1[-5:]
        )

        total_volume = sum(
            float(x[7])
            for x in c1[-5:]
        )

        buyer_percent = (
            buy_volume / total_volume * 100
            if total_volume > 0
            else 50
        )


        ema9 = ema(close, 9)
        ema21 = ema(close, 21)
        ema50 = ema(close, 50)

        ema9_prev = ema(
            close[:-3],
            9
        )

        ema21_prev = ema(
            close[:-3],
            21
        )

        ema_up = (
            ema9 > ema21
            and
            ema9 > ema9_prev
        )

        ema_cross = (
            ema9 > ema21
            and
            ema9_prev <= ema21_prev
        )


        current_rsi = rsi(close)
        previous_rsi = rsi(close[:-3])


        _, _, macd_hist = macd(close)

        _, _, previous_macd_hist = macd(
            close[:-3]
        )


        adx_value, plus_di, minus_di = adx(
            high,
            low,
            close
        )


        lower, middle, upper = bb(close)

        width = (
            (upper - lower)
            / middle
            * 100
            if middle > 0
            else 0
        )

        old_lower, old_middle, old_upper = bb(
            close[:-5]
        )

        old_width = (
            (old_upper - old_lower)
            / old_middle
            * 100
            if old_middle > 0
            else width
        )

        squeeze = (
            width <= 2.2
            or
            (
                old_width > 0
                and
                width < old_width * 0.80
            )
        )

        expanding = (
            old_width > 0
            and
            width > old_width * 1.08
        )


        resistance = max(
            high[-30:-2]
        )

        distance = max(
            0,
            (
                resistance - price
            )
            / price
            * 100
        )

        breakout = price > resistance
        closed_breakout = (
            close[-1] > resistance
        )

        near_resistance = (
            distance <= 0.35
        )


        candle_range = (
            high[-1] - low[-1]
        )

        close_position = (
            (
                close[-1] - low[-1]
            )
            /
            candle_range
            * 100
            if candle_range > 0
            else 50
        )


        higher_low = (
            low[-1] > low[-3]
            and
            low[-3] >= low[-6]
        )


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
            35 <= current_rsi <= 65
            and
            current_rsi > previous_rsi
        ):
            setup += 8

        if price >= ema50:
            setup += 5

        if (
            near_resistance
            or
            distance <= 0.70
        ):
            setup += 8

        if vr >= 1.5:
            setup += 8

        if buyer_percent >= 58:
            setup += 5

        if impulse >= 1.4:
            setup += 4


        confirmation = 0

        if closed_breakout:
            confirmation += 18

        elif breakout:
            confirmation += 14

        if vr >= 2.0:
            confirmation += 12

        elif vr >= 1.5:
            confirmation += 7

        if vr5 >= 1.5:
            confirmation += 8

        if buyer_percent >= 65:
            confirmation += 7

        if macd_hist > previous_macd_hist:
            confirmation += 6

        if (
            plus_di > minus_di
            and
            adx_value >= 18
        ):
            confirmation += 7

        if close_position >= 65:
            confirmation += 4

        if expanding:
            confirmation += 4

        if impulse >= 1.8:
            confirmation += 5


        penalty = 0

        if momentum1 > 2.5:
            penalty -= 10

        if momentum5 > 5:
            penalty -= 12

        if current_rsi > 78:
            penalty -= 10

        if (
            buyer_percent < 50
            and
            vr >= 1.8
        ):
            penalty -= 8

        if (
            momentum5 < -1.2
            and
            not higher_low
        ):
            penalty -= 12


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
            score >= 70
            and
            confirmation >= 22
            and
            (
                closed_breakout
                or
                (
                    vr >= 2.0
                    and
                    buyer_percent >= 60
                )
            )
        ):
            stage = "CONFIRMED"

        if (
            score >= 86
            and
            confirmation >= 30
            and
            vr >= 1.7
            and
            buyer_percent >= 62
            and
            (
                closed_breakout
                or
                impulse >= 1.8
            )
        ):
            stage = "VERY"


        if stage == "VERY":
            level = "VERY"

        elif stage == "CONFIRMED":
            level = "BUY"

        else:
            level = "PASS"


        DBS.put(
            symbol,
            score,
            level,
            stage
        )


        if level not in (
            "BUY",
            "VERY"
        ):
            return {
                "status": "PASS",
                "score": score
            }


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
            "bp": buyer_percent,
            "vr": vr,
            "vr5": vr5,
            "imp": impulse,
            "rv": current_rsi,
            "ad": adx_value,
            "dist": distance,
            "ema": ema_up,
            "macd": (
                macd_hist
                >
                previous_macd_hist
            ),
            "squeeze": squeeze,
            "hl": higher_low,
            "breakout": breakout
        }


    except Exception as e:

        log.debug(
            "%s: %s",
            symbol,
            e
        )

        return {
            "status": "error"
            }
def message(result):

    if result["status"] == "VERY":
        title = "🔥 ÇOK GÜÇLÜ AL"
    else:
        title = "🟢 AL"

    reasons = []

    if result["breakout"]:
        reasons.append("Direnç kırıldı")
    elif result["dist"] <= 0.35:
        reasons.append(
            f"Direnç %{result['dist']:.2f}"
        )

    if result["vr"] >= 1.5:
        reasons.append(
            f"1m hacim {result['vr']:.1f}x"
        )

    if result["vr5"] >= 1.5:
        reasons.append(
            f"5m hacim {result['vr5']:.1f}x"
        )

    if result["imp"] >= 1.4:
        reasons.append(
            f"Hacim ivmesi {result['imp']:.1f}x"
        )

    if result["bp"] >= 65:
        reasons.append(
            f"Alıcı %{result['bp']:.0f}"
        )

    if result["ema"]:
        reasons.append("EMA trend")

    if result["macd"]:
        reasons.append("MACD güçleniyor")

    if result["hl"]:
        reasons.append("Higher-Low")

    if result["squeeze"]:
        reasons.append("BB sıkışma")

    return (
        "🐋 BALİNA RADARI V21\n\n"
        f"{title}\n\n"
        f"🪙 #{result['symbol']}\n"
        f"💰 {result['price']:.8g}\n"
        f"💪 Güç: {result['score']}/100\n\n"
        f"📊 1m Hacim: {result['vr']:.2f}x | "
        f"5m: {result['vr5']:.2f}x\n"
        f"🚀 İvme: {result['imp']:.2f}x\n"
        f"🛒 Alıcı: %{result['bp']:.0f}\n"
        f"📈 RSI: {result['rv']:.0f} | "
        f"ADX: {result['ad']:.0f}\n"
        f"🎯 Direnç: %{result['dist']:.2f}\n"
        f"🚀 Kırılım: "
        f"{'✅' if result['breakout'] else '⏳'}\n\n"
        f"🔎 {' • '.join(reasons[:7])}\n\n"
        f"{'🚀 Güçlü teyit.' if result['status'] == 'VERY' else '🎯 Alım teyidi oluştu.'}"
    )


def scan():

    start = time.time()

    data = tickers()

    if not data:
        return True

    price_map = {}

    for item in data:

        try:

            symbol = item.get("symbol")

            price_map[symbol] = float(
                item.get("lastPrice", 0)
            )

        except (TypeError, ValueError):

            continue

    DBS.update_outcomes(
        price_map
    )

    all_candidates = candidates(
        data
    )

    symbols = shortlist(
        all_candidates
    )

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
            for item in symbols
        ]

        for job in as_completed(jobs):

            result = job.result()

            status = result.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0)
                +
                1
            )

            if status in (
                "BUY",
                "VERY"
            ):

                signals.append(
                    result
                )

    rank = {
        "BUY": 1,
        "VERY": 2
    }

    signals.sort(
        key=lambda x: (
            rank[x["status"]],
            x["score"]
        ),
        reverse=True
    )

    sent = 0

    for result in signals[
        :MAX_SIGNALS
    ]:

        if not DBS.can_send(
            result["symbol"],
            result["status"]
        ):
            continue

        if telegram(
            message(result)
        ):

            DBS.put(
                result["symbol"],
                result["score"],
                result["status"],
                result["status"],
                sent=time.time()
            )

            DBS.create_signal(
                result["symbol"],
                result["price"],
                result["score"],
                result["setup"],
                result["confirmation"],
                result["penalty"],
                result["status"]
            )

            sent += 1

        time.sleep(0.3)

    elapsed = (
        time.time()
        -
        start
    )

    errors = stats.get(
        "error",
        0
    )

    log.info(
        "V21 | TRY:%d/%d | AL:%d | "
        "VERY:%d | Hata:%d | "
        "Gonder:%d | %.1fs",
        len(symbols),
        len(all_candidates),
        stats.get("BUY", 0),
        stats.get("VERY", 0),
        errors,
        sent,
        elapsed
    )

    return (
        errors
        /
        max(1, len(symbols))
        > 0.30
        or
        elapsed
        >
        SCAN_INTERVAL * 1.25
    )
app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V21 Aktif"


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V21",
        "base": BASE,
        "scan_interval": SCAN_INTERVAL
    }


@app.route("/performance")
def performance():

    rows = DBS.performance_summary()

    if not rows:
        return {
            "samples": 0,
            "note": "Henüz 15dk+ tamamlanmış sinyal yok."
        }

    n = len(rows)

    avg_max = sum(
        r[3] for r in rows
    ) / n

    avg_min = sum(
        r[4] for r in rows
    ) / n

    avg_c15 = sum(
        r[6] for r in rows
    ) / n

    return {
        "samples": n,
        "avg_max_pct": round(
            avg_max,
            2
        ),
        "avg_min_pct": round(
            avg_min,
            2
        ),
        "avg_15m_pct": round(
            avg_c15,
            2
        )
    }


def validate_market():

    info = exchange_info()

    if not isinstance(info, dict):
        raise RuntimeError(
            "Binance TR exchangeInfo geçersiz."
        )

    symbols = {
        item.get("symbol")
        for item in info.get(
            "symbols",
            []
        )
        if item.get("symbol")
    }

    try_symbols = [
        symbol
        for symbol in symbols
        if symbol.endswith("TRY")
    ]

    if not try_symbols:
        raise RuntimeError(
            f"BASE {BASE} üzerinde "
            "TRY marketi bulunamadı."
        )

    log.info(
        "V21 | TRY market doğrulandı | "
        "TRY:%d | Örnek:%s",
        len(try_symbols),
        ",".join(
            sorted(try_symbols)[:5]
        )
    )


def loop():

    log.info(
        "🐋 BALİNA RADARI V21 "
        "başlatılıyor..."
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
            "🐋 BALİNA RADARI V21 AKTİF\n"
            "🟢 AL → 🔥 ÇOK GÜÇLÜ AL"
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
            time.time()
            -
            started
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
    name="balina-v21"
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
