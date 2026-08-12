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
class DB:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        with sqlite3.connect(path) as d:
            d.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    stage INTEGER,
                    score REAL,
                    level TEXT,
                    sent REAL
                )
            """)
            d.execute("""
                CREATE TABLE IF NOT EXISTS oi(
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    ts REAL
                )
            """)

    def get_state(self, symbol):
        with self.lock, sqlite3.connect(self.path) as d:
            return d.execute(
                "SELECT stage,score,level,sent FROM state WHERE symbol=?",
                (symbol,)
            ).fetchone()

    def save_state(self, symbol, stage, score, level):
        with self.lock, sqlite3.connect(self.path) as d:
            d.execute("""
                INSERT INTO state VALUES(?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    stage=excluded.stage,
                    score=excluded.score,
                    level=excluded.level
            """, (
                symbol,
                stage,
                score,
                level,
                time.time()
            ))

    def can_send(self, symbol, level):
        r = self.get_state(symbol)

        if not r:
            return True

        old_stage, _, old_level, sent = r

        rank = {
            "AL": 1,
            "VERY": 2
        }

        if rank.get(level, 0) > rank.get(old_level, 0):
            return True

        return time.time() - sent >= ALERT_COOLDOWN

    def mark_sent(self, symbol, stage, score, level):
        with self.lock, sqlite3.connect(self.path) as d:
            d.execute("""
                INSERT INTO state VALUES(?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    stage=excluded.stage,
                    score=excluded.score,
                    level=excluded.level,
                    sent=excluded.sent
            """, (
                symbol,
                stage,
                score,
                level,
                time.time()
            ))

    def get_oi(self, symbol):
        with self.lock, sqlite3.connect(self.path) as d:
            r = d.execute(
                "SELECT value,ts FROM oi WHERE symbol=?",
                (symbol,)
            ).fetchone()

        if not r:
            return None

        if time.time() - r[1] > SCAN_INTERVAL * 5:
            return None

        return float(r[0])

    def put_oi(self, symbol, value):
        if value is None:
            return

        with self.lock, sqlite3.connect(self.path) as d:
            d.execute("""
                INSERT INTO oi VALUES(?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
            """, (
                symbol,
                value,
                time.time()
            ))


DBS = DB(DB_PATH)


def tr_symbols(tickers):
    out = []

    for x in tickers:
        s = x.get("symbol", "")

        if not s.endswith("TRY"):
            continue

        if any(
            z in s
            for z in (
                "UPTRY",
                "DOWNTRY",
                "BULLTRY",
                "BEARTRY"
            )
        ):
            continue

        try:
            volume = float(x.get("quoteVolume", 0))

            if volume >= MIN_VOLUME_TRY:
                out.append(s)

        except (TypeError, ValueError):
            continue

    return out


def build_candidate_map(tickers):
    result = {}

    for x in tickers:
        s = x.get("symbol", "")

        if not s.endswith("TRY"):
            continue

        try:
            result[s] = {
                "price": float(x.get("lastPrice", 0)),
                "volume": float(x.get("quoteVolume", 0)),
                "change": float(x.get("priceChangePercent", 0))
            }
        except (TypeError, ValueError):
            pass

    return result
def analyze(symbol):
    try:
        sp = tr_klines(symbol, "1m", 180)
        sp5 = tr_klines(symbol, "5m", 60)

        if len(sp) < 100 or len(sp5) < 25:
            return {"status": "PASS"}

        live = sp[-1]
        price = float(live[4])

        c = [float(x[4]) for x in sp[:-1]]
        h = [float(x[2]) for x in sp[:-1]]
        l = [float(x[3]) for x in sp[:-1]]
        o = [float(x[1]) for x in sp[:-1]]

        c5 = [float(x[4]) for x in sp5[:-1]]

        m1 = pct(c[-2], price)
        m3 = pct(c[-4], price)
        m5 = pct(c5[-2], price)
        m15 = pct(c5[-4], price)

        lo30 = min(l[-30:])
        hi30 = max(h[-30:])

        loc30 = (
            (price - lo30) /
            (hi30 - lo30) * 100
            if hi30 > lo30 else 50
        )

        lo60 = min(l[-60:])
        hi60 = max(h[-60:])

        loc60 = (
            (price - lo60) /
            (hi60 - lo60) * 100
            if hi60 > lo60 else 50
        )

        sv = [float(x[7]) for x in sp[:-1]]
        tv = [float(x[8]) for x in sp[:-1]]

        av = avg(sv[-36:])
        at = avg(tv[-36:])

        if av <= 0 or at <= 0:
            return {"status": "PASS"}

        volume_ratio = avg(sv[-3:]) / av
        trade_ratio = avg(tv[-3:]) / at

        old_volume = avg(sv[-9:-3])

        volume_impulse = (
            avg(sv[-3:]) / old_volume
            if old_volume > 0 else 1
        )

        buy = sum(float(x[10]) for x in sp[-4:])
        total = sum(float(x[7]) for x in sp[-4:])

        buyer = (
            buy / total * 100
            if total > 0 else 50
        )

        e9 = ema(c, 9)
        e21 = ema(c, 21)
        e50 = ema(c, 50)

        e9_old = ema(c[:-3], 9)
        e21_old = ema(c[:-3], 21)

        ema_bull = e9 > e21
        ema_rising = e9 > e9_old
        ema_cross = e9 > e21 and e9_old <= e21_old

        rv = rsi(c)
        rv_old = rsi(c[:-3])

        rsi_rising = rv > rv_old

        mm, ms, mh = macd(c)
        pm, ps, ph = macd(c[:-3])

        macd_rising = mh > ph
        macd_bull = mm > ms

        ad, di, mdi = adx(h, l, c)

        trend = ad >= 18 and di > mdi

        bl, bm, bu = bollinger(c)

        width = (
            (bu - bl) / bm * 100
            if bm else 0
        )

        old_bl, old_bm, old_bu = bollinger(c[:-5])

        old_width = (
            (old_bu - old_bl) /
            old_bm * 100
            if old_bm else width
        )

        squeeze = (
            width <= 1.8
            or width < old_width * 0.82
        )

        expanding = (
            width > old_width * 1.05
            if old_width else False
        )

        resistance = max(h[-20:])

        distance = max(
            0,
            (resistance - price) /
            price * 100
        )

        recent_high = max(h[-8:])

        breakout_now = price >= recent_high * 0.998

        higher_low = (
            l[-1] > l[-3]
            and l[-3] >= l[-6]
        )

        candle_range = h[-1] - l[-1]

        close_strength = (
            (c[-1] - l[-1]) /
            candle_range
            if candle_range > 0 else 0.5
        )

        strong_close = close_strength >= 0.75

        dip_score = 0

        if loc30 <= 25:
            dip_score += 10
        elif loc30 <= 40:
            dip_score += 6

        if loc60 <= 30:
            dip_score += 5

        money_score = 0

        if volume_ratio >= 3:
            money_score += 12
        elif volume_ratio >= 2:
            money_score += 9
        elif volume_ratio >= 1.5:
            money_score += 6
        elif volume_ratio >= 1.2:
            money_score += 3

        if trade_ratio >= 2:
            money_score += 5
        elif trade_ratio >= 1.5:
            money_score += 3

        if buyer >= 75:
            money_score += 7
        elif buyer >= 68:
            money_score += 5
        elif buyer >= 60:
            money_score += 3

        momentum_score = 0

        if ema_bull:
            momentum_score += 4

        if ema_rising:
            momentum_score += 3

        if ema_cross:
            momentum_score += 4

        if rsi_rising and 40 <= rv <= 68:
            momentum_score += 5
        elif rsi_rising and 35 <= rv <= 72:
            momentum_score += 3

        if macd_rising:
            momentum_score += 4

        if macd_bull:
            momentum_score += 3

        if trend:
            momentum_score += 4

        if price >= e50:
            momentum_score += 2

        breakout_score = 0

        if distance <= 0.10:
            breakout_score += 10
        elif distance <= 0.25:
            breakout_score += 8
        elif distance <= 0.50:
            breakout_score += 5
        elif distance <= 0.80:
            breakout_score += 2

        if breakout_now:
            breakout_score += 6

        if squeeze:
            breakout_score += 4

        if expanding and volume_impulse >= 1.25:
            breakout_score += 4

        if higher_low:
            breakout_score += 4

        if strong_close:
            breakout_score += 3

        risk = 0

        if m15 < -1.5 and not higher_low:
            risk -= 12

        if rv > 80:
            risk -= 10
        elif rv > 75:
            risk -= 5

        if m5 > 4:
            risk -= 10
        elif m5 > 2.5:
            risk -= 5

        if buyer < 55 and volume_ratio >= 2:
            risk -= 10

        score = clamp(
            dip_score +
            money_score +
            momentum_score +
            breakout_score +
            risk
        )

        oi_change = None

        if score >= 68:
            now_oi = futures_oi(
                symbol.replace("TRY", "USDT")
            )

            old_oi = DBS.get_oi(symbol)

            if now_oi is not None and old_oi is not None:
                oi_change = pct(
                    old_oi,
                    now_oi
                )

                if oi_change >= 0.7:
                    score = clamp(score + 3)

                elif oi_change <= -1.5:
                    score = clamp(score - 3)

            DBS.put_oi(
                symbol,
                now_oi
            )

        state = DBS.get_state(symbol)

        previous_stage = (
            state[0]
            if state else 0
        )

        previous_score = (
            float(state[1])
            if state else 0
        )

        if score >= previous_score + 5:
            score = clamp(score + 3)

        # -------------------------------------------------
        # YENİ HAREKET / ESKİ PUMP AYRIMI
        # -------------------------------------------------

        fresh_move = (
            volume_impulse >= 1.35
            and volume_ratio >= 1.4
            and (
                breakout_now
                or distance <= 0.35
            )
        )

        late_move = (
            m5 > 5
            or m15 > 9
            or (
                rv > 82
                and distance > 1.0
            )
        )

        # -------------------------------------------------
        # İÇ AŞAMALAR
        # -------------------------------------------------

        preparation = (
            dip_score >= 6
            and (
                squeeze
                or volume_ratio >= 1.2
            )
        )

        strengthening = (
            momentum_score >= 10
            and money_score >= 8
            and (
                volume_ratio >= 1.2
                or rsi_rising
                or macd_rising
            )
        )

        confirmed = (
            score >= 78
            and money_score >= 13
            and momentum_score >= 12
            and breakout_score >= 10
            and volume_ratio >= 1.25
            and buyer >= 58
            and not late_move
        )

        very_confirmed = (
            score >= 90
            and money_score >= 17
            and momentum_score >= 16
            and breakout_score >= 14
            and volume_ratio >= 1.5
            and volume_impulse >= 1.15
            and buyer >= 62
            and not late_move
        )

        if very_confirmed:
            level = "VERY"
            stage = 4

        elif confirmed:
            level = "AL"
            stage = 3

        elif strengthening:
            level = "INTERNAL"
            stage = 2

        elif preparation:
            level = "INTERNAL"
            stage = 1

        else:
            level = "PASS"
            stage = 0

        DBS.save_state(
            symbol,
            stage,
            score,
            level
        )

        if level == "INTERNAL" or level == "PASS":
            return {
                "status": "PASS",
                "score": score,
                "stage": stage
            }

        reasons = []

        if volume_ratio >= 1.4:
            reasons.append(
                f"Hacim {volume_ratio:.2f}x"
            )

        if volume_impulse >= 1.3:
            reasons.append(
                f"İvme {volume_impulse:.2f}x"
            )

        if buyer >= 65:
            reasons.append(
                f"Alıcı %{buyer:.0f}"
            )

        if ema_bull:
            reasons.append("EMA9>21")

        if ema_rising:
            reasons.append("EMA yükseliyor")

        if rsi_rising:
            reasons.append(
                f"RSI {rv:.0f}↑"
            )

        if macd_rising:
            reasons.append(
                "MACD güçleniyor"
            )

        if trend:
            reasons.append(
                f"ADX {ad:.0f}"
            )

        if squeeze:
            reasons.append(
                "BB sıkışma"
            )

        if breakout_now:
            reasons.append(
                "Kırılım"
            )

        if higher_low:
            reasons.append(
                "Higher-Low"
            )

        if strong_close:
            reasons.append(
                "Güçlü kapanış"
            )

        if fresh_move:
            reasons.append(
                "Yeni hareket"
            )

        return {
            "status": level,
            "symbol": symbol,
            "score": score,
            "price": price,
            "loc": loc30,
            "buyer": buyer,
            "volume": volume_ratio,
            "futures_volume": 0,
            "impulse": volume_impulse,
            "rsi": rv,
            "ema": ema_bull,
            "macd": macd_rising,
            "adx": ad,
            "squeeze": squeeze,
            "distance": distance,
            "higher_low": higher_low,
            "oi": oi_change,
            "fresh": fresh_move,
            "reasons": reasons
        }

    except Exception as e:
        log.debug(
            "%s analyze: %s",
            symbol,
            e
        )

        return {
            "status": "error"
        }
def message(r):
    if r["status"] == "VERY":
        title = "🔥 ÇOK GÜÇLÜ AL"
        text = "🚀 Çoklu teyit tamamlandı."
    else:
        title = "🟢 AL"
        text = "🎯 Teknik yapı teyit aldı."

    oi = (
        "—"
        if r["oi"] is None
        else f"{r['oi']:+.2f}%"
    )

    return (
        f"🐋 BALİNA RADARI V20\n\n"
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r['price']:.8g}\n"
        f"💪 GÜÇ: {r['score']}/100\n\n"
        f"📈 EMA9/21: "
        f"{'🟢' if r['ema'] else '🔴'}\n"
        f"📊 RSI: {r['rsi']:.0f}\n"
        f"〽️ MACD: "
        f"{'🟢' if r['macd'] else '🔴'}\n"
        f"⚡ ADX: {r['adx']:.0f}\n\n"
        f"💥 Hacim: {r['volume']:.2f}x\n"
        f"🚀 Hacim ivmesi: {r['impulse']:.2f}x\n"
        f"🟢 Alıcı: %{r['buyer']:.0f}\n"
        f"🎯 Direnç: %{r['distance']:.2f}\n"
        f"📦 BB: "
        f"{'🟢 Sıkışma' if r['squeeze'] else '—'}\n"
        f"📈 Higher-Low: "
        f"{'✅' if r['higher_low'] else '—'}\n"
        f"💰 OI: {oi}\n\n"
        f"🔎 {' • '.join(r['reasons'][:8])}\n\n"
        f"{text}\n"
        f"⚠️ Teknik filtredir; risk yönetimi sana aittir."
    )


def scan():
    started = time.time()

    tickers = tr_tickers()

    if not tickers:
        log.warning("Binance TR ticker alınamadı.")
        return True

    symbols = tr_symbols(tickers)

    if not symbols:
        log.warning("Binance TR aday bulunamadı.")
        return False

    signals = []
    stats = {}

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as ex:

        jobs = [
            ex.submit(
                analyze,
                s
            )
            for s in symbols
        ]

        for job in as_completed(jobs):

            r = job.result()

            status = r.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0) + 1
            )

            if status in (
                "AL",
                "VERY"
            ):
                signals.append(r)

    rank = {
        "AL": 1,
        "VERY": 2
    }

    signals.sort(
        key=lambda x: (
            rank[x["status"]],
            x["score"],
            x["volume"],
            x["buyer"]
        ),
        reverse=True
    )

    sent = 0

    for r in signals[:MAX_ALERTS]:

        symbol = r["symbol"]
        level = r["status"]

        if not DBS.can_send(
            symbol,
            level
        ):
            continue

        if telegram(
            message(r)
        ):
            DBS.mark_sent(
                symbol,
                r.get("stage", 3),
                r["score"],
                level
            )
            sent += 1

        time.sleep(0.5)

    elapsed = time.time() - started

    errors = stats.get(
        "error",
        0
    )

    total = max(
        1,
        len(symbols)
    )

    log.info(
        "V20 | TR:%d | AL:%d | VERY:%d | "
        "Hata:%d | Gonder:%d | %.1fs",
        len(symbols),
        stats.get("AL", 0),
        stats.get("VERY", 0),
        errors,
        sent,
        elapsed
    )

    return (
        errors / total > 0.30
        or elapsed > SCAN_INTERVAL * 1.25
    )
app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 BALİNA RADARI V20 AKTİF"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V20",
        "market": "Binance TR",
        "scan_interval": SCAN_INTERVAL,
        "telegram": [
            "AL",
            "VERY"
        ]
    }


def loop():

    log.info(
        "🐋 BALİNA RADARI V20 başlatılıyor..."
    )

    if TOKEN and CHAT:
        telegram(
            "🐋 BALİNA RADARI V20 AKTİF\n\n"
            "🇹🇷 Binance TR ana piyasa\n"
            "🔇 Hazırlık mesajları kapalı\n"
            "🔇 Takip mesajları kapalı\n"
            "🟢 AL\n"
            "🔥 ÇOK GÜÇLÜ AL"
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

            wait = max(
                180,
                SCAN_INTERVAL * 3
            )

            log.warning(
                "Koruma beklemesi: %ds",
                wait
            )

            time.sleep(wait)

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
    name="balina-v20"
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
