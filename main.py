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
# =========================
# ANA ANALİZ
# =========================

def analyze(symbol):

    try:

        # Daha geniş veri:
        # ani hareketleri yakalamak için
        # 1m + 5m birlikte kullanılıyor.
        sp = klines(
            SPOT,
            symbol,
            "1m",
            180
        )

        fu = klines(
            FUT,
            symbol,
            "1m",
            90
        )

        sp5 = klines(
            SPOT,
            symbol,
            "5m",
            48
        )

        if (
            len(sp) < 90
            or len(fu) < 50
            or len(sp5) < 25
        ):
            return {
                "status": "PASS"
            }


        # Son mum canlı olabilir.
        live = sp[-1]

        price = float(live[4])


        # Tamamlanmış mumlar
        c = [
            float(x[4])
            for x in sp[:-1]
        ]

        h = [
            float(x[2])
            for x in sp[:-1]
        ]

        l = [
            float(x[3])
            for x in sp[:-1]
        ]

        o = [
            float(x[1])
            for x in sp[:-1]
        ]


        c5 = [
            float(x[4])
            for x in sp5[:-1]
        ]


        # =========================
        # FİYAT MOMENTUMU
        # =========================

        m1 = pct(
            c[-2],
            price
        )

        m5 = pct(
            c5[-2],
            price
        )

        m15 = pct(
            c5[-4],
            price
        )


        # =========================
        # SON 60 DAKİKALIK KONUM
        # =========================

        lo = min(l[-60:])
        hi = max(h[-60:])

        loc = (
            (price - lo) /
            (hi - lo) * 100
            if hi > lo
            else 50
        )


        # Dip artık sadece hazırlık puanı.
        # AL için zorunlu değil.

        prep = 0

        if loc <= 25:
            prep += 8

        elif loc <= 40:
            prep += 5


        # =========================
        # HACİM
        # =========================

        sv = [
            float(x[7])
            for x in sp[:-1]
        ]

        fv = [
            float(x[7])
            for x in fu[:-1]
        ]

        tv = [
            float(x[8])
            for x in sp[:-1]
        ]


        avs = avg(sv[-36:])
        avf = avg(fv[-36:])
        avt = avg(tv[-36:])


        if min(avs, avf, avt) <= 0:
            return {
                "status": "PASS"
            }


        sr = (
            avg(sv[-3:]) /
            avs
        )

        fr = (
            avg(fv[-3:]) /
            avf
        )

        trr = (
            avg(tv[-3:]) /
            avt
        )


        # Son 3 dakikanın,
        # ondan önceki 3 dakikaya göre ivmesi.
        prevv = avg(sv[-6:-3])

        imp = (
            avg(sv[-3:]) / prevv
            if prevv > 0
            else 1
        )


        # Taker buy quote volume
        buy = sum(
            float(x[10])
            for x in sp[-4:]
        )

        total = sum(
            float(x[7])
            for x in sp[-4:]
        )

        bp = (
            buy / total * 100
            if total > 0
            else 50
        )


        # Spot öncülüğü
        spot_lead = (
            sr >= 1.25
            and sr >= fr * 1.10
        )


        # =========================
        # PARA PUANI
        # =========================

        money = 0

        if sr >= 2.8:
            money += 11

        elif sr >= 2.0:
            money += 8

        elif sr >= 1.5:
            money += 6

        elif sr >= 1.25:
            money += 3


        if fr >= 2.2:
            money += 6

        elif fr >= 1.5:
            money += 4

        elif fr >= 1.2:
            money += 2


        if trr >= 2.0:
            money += 5

        elif trr >= 1.5:
            money += 3


        if bp >= 72:
            money += 6

        elif bp >= 65:
            money += 4

        elif bp >= 60:
            money += 2


        if spot_lead:
            money += 3


        # =========================
        # EMA
        # =========================

        e9 = ema(c, 9)
        e21 = ema(c, 21)
        e50 = ema(c, 50)


        e9p = ema(c[:-3], 9)
        e21p = ema(c[:-3], 21)


        ema_up = e9 > e21

        ema_accel = e9 > e9p

        ema_turn = (
            e9 > e9p
            and e9p <= e21p
        )


        # =========================
        # RSI
        # =========================

        rv = rsi(c)

        rvp = rsi(c[:-3])

        rsi_up = rv > rvp


        # =========================
        # MACD
        # =========================

        mm, ms, mh = macd(c)

        pm, ps, ph = macd(c[:-3])

        macd_up = mh > ph

        macd_bull = mm > ms


        # =========================
        # ADX
        # =========================

        ad, di, mdi = adx(
            h,
            l,
            c
        )

        trend_power = (
            ad >= 18
            and di > mdi
        )


        # =========================
        # MOMENTUM PUANI
        # =========================

        momentum = 0

        if ema_up:
            momentum += 4

        if ema_accel:
            momentum += 3

        if ema_turn:
            momentum += 2

        if rsi_up:

            if 40 <= rv <= 65:
                momentum += 4

            elif 35 <= rv <= 70:
                momentum += 2


        if macd_up:
            momentum += 4

        if macd_bull:
            momentum += 3

        if trend_power:
            momentum += 4

        if price >= e50:
            momentum += 2


        # =========================
        # BOLLINGER
        # =========================

        bl, bm, bu = bb(c)

        width = (
            (bu - bl) /
            bm * 100
            if bm
            else 0
        )


        old_l, old_m, old_u = bb(
            c[:-5]
        )

        old_width = (
            (old_u - old_l) /
            old_m * 100
            if old_m
            else width
        )


        squeeze = (
            width <= 1.8
            or width < old_width * 0.82
        )


        expanding = (
            width > old_width * 1.05
            if old_width
            else False
        )


        # =========================
        # KIRILIM
        # =========================

        recent_high = max(h[-20:])

        dist = max(
            0,
            (recent_high - price) /
            price * 100
        )


        breakout = 0


        # Dirence yaklaşma
        if dist <= 0.10:
            breakout += 10

        elif dist <= 0.25:
            breakout += 8

        elif dist <= 0.50:
            breakout += 5

        elif dist <= 0.80:
            breakout += 2


        if squeeze:
            breakout += 4


        if expanding and imp >= 1.25:
            breakout += 4


        # Higher-Low
        higher_low = (
            l[-1] > l[-3]
            and l[-3] >= l[-6]
        )

        if higher_low:
            breakout += 4


        # Son mum güçlü kapanışa yakın mı?
        candle_range = (
            h[-1] - l[-1]
        )

        close_position = (
            (c[-1] - l[-1]) /
            candle_range
            if candle_range > 0
            else 0.5
        )


        if close_position >= 0.75:
            breakout += 3


        # =========================
        # RİSK
        # =========================

        risk = 0

        falling = (
            m5 < -0.8
            and m15 < -1.2
            and not higher_low
        )


        if falling:
            risk -= 15


        # Aşırı alım
        if rv > 78:
            risk -= 8

        elif rv > 72:
            risk -= 4


        # Ani pump'ın tepesinden kovalamayı azalt
        if m5 > 3.0:
            risk -= 8

        elif m5 > 2.0:
            risk -= 4


        # Alıcı baskısı zayıfsa
        if bp < 55 and sr >= 1.5:
            risk -= 10


        # Hacim yok ama fiyat uçmuşsa
        if imp < 0.75 and m5 > 1:
            risk -= 6


        # =========================
        # TOPLAM SKOR
        # =========================

        score = clamp(
            prep +
            money +
            momentum +
            breakout +
            risk
        )


        # =========================
        # İÇ HAFIZA
        # =========================

        previous_memory = remember(
            symbol,
            score
        )


        if previous_memory:

            old_score = previous_memory["score"]

            # Coin güçleniyorsa küçük bonus
            if score >= old_score + 5:
                score = clamp(score + 4)


        # =========================
        # OI
        # =========================

        oi_change = None

        if score >= 70:

            now_oi = oi(symbol)

            old_oi = DBS.getoi(symbol)

            if (
                old_oi is not None
                and now_oi is not None
            ):

                oi_change = pct(
                    old_oi,
                    now_oi
                )

                if oi_change >= 0.7:
                    score = clamp(score + 3)

                elif oi_change <= -1.5:
                    score = clamp(score - 3)


            DBS.putoi(
                symbol,
                now_oi
            )


        # =========================
        # AL KOŞULLARI
        # =========================

        # DİKKAT:
        # Burada artık dip şartı yok.

        buy = (
            score >= 78
            and money >= 14
            and momentum >= 12
            and breakout >= 9
            and sr >= 1.25
            and bp >= 58
            and not falling
            and not (
                rv > 78
                and m5 > 2.5
            )
        )


        # =========================
        # ÇOK GÜÇLÜ AL
        # =========================

        very = (
            score >= 90
            and money >= 18
            and momentum >= 16
            and breakout >= 14
            and sr >= 1.5
            and bp >= 62
            and imp >= 1.15
            and not falling
            and rv < 78
        )


        if very:
            level = "VERY"

        elif buy:
            level = "AL"

        else:
            # Hazırlık aşamaları burada kalıyor.
            # Telegram'a GÖNDERİLMEYECEK.
            level = "PASS"


        if level == "PASS":

            return {
                "status": "PASS",
                "score": score
            }


        # =========================
        # SONUÇ
        # =========================

        reasons = []

        if sr >= 1.5:
            reasons.append(
                f"Spot {sr:.2f}x"
            )

        elif sr >= 1.25:
            reasons.append(
                f"Spot {sr:.2f}x"
            )


        if bp >= 65:
            reasons.append(
                f"Alıcı %{bp:.0f}"
            )

        elif bp >= 60:
            reasons.append(
                f"Alıcı %{bp:.0f}"
            )


        if ema_up:
            reasons.append(
                "EMA9>21"
            )


        if rsi_up and 35 < rv < 70:
            reasons.append(
                f"RSI {rv:.0f}↑"
            )


        if macd_up:
            reasons.append(
                "MACD güçleniyor"
            )


        if macd_bull:
            reasons.append(
                "MACD pozitif"
            )


        if ad >= 20 and di > mdi:
            reasons.append(
                f"ADX {ad:.0f}"
            )


        if squeeze:
            reasons.append(
                "BB sıkışma"
            )


        if dist <= 0.5:
            reasons.append(
                f"Direnç %{dist:.2f}"
            )


        if higher_low:
            reasons.append(
                "Higher-Low"
            )


        if imp >= 1.3:
            reasons.append(
                f"Hacim {imp:.2f}x"
            )


        if oi_change is not None and oi_change >= 0.7:
            reasons.append(
                f"OI +{oi_change:.2f}%"
            )


        return {
            "status": level,
            "symbol": symbol,
            "score": score,
            "price": price,
            "loc": loc,
            "bp": bp,
            "sr": sr,
            "fr": fr,
            "trr": trr,
            "rv": rv,
            "ema_up": ema_up,
            "macd_up": macd_up,
            "macd_bull": macd_bull,
            "squeeze": squeeze,
            "dist": dist,
            "imp": imp,
            "higher_low": higher_low,
            "spot_lead": spot_lead,
            "adx": ad,
            "oi": oi_change,
            "reasons": reasons
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
