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
# 🐋 BALİNA RADARI V14 — STRATEJİ FİLTRESİ
# ============================================================
# AMAÇ:
#   Çok sayıda teknik mesaj üretmek yerine, kullanıcının hedeflediği
#   yapıya en yakın coinleri seçmek:
#
#   DİP/BİRİKİM → DÖNÜŞ → ERKEN HAREKET
#
# V14'te puan "hacim ne kadar büyük?" sorusundan çok:
#   1) Fiyat son hareketin neresinde?
#   2) Dip/birikim yapısı var mı?
#   3) Satış zayıflayıp alıcılar devreye giriyor mu?
#   4) Spot akışı gerçek mi?
#   5) Dönüş gerçekten başladı mı?
#   6) Coin zaten kaçmış mı?
# sorularına ağırlık verir.
#
# ÖNEMLİ:
#   Bot gerçek piyasa dibini bilemez. "DİP" ifadesi burada
#   "yakın dönem yerel dip/birikim bölgesi" anlamındadır.
#
# TELEGRAM:
#   Varsayılan olarak yalnızca STRONG / CANDIDATE gönderilir.
#   Böylece mesaj kalabalığı azaltılır.
#
#   STRONG  >= 86
#   CANDIDATE >= 78
#   WATCH/ACCUM dahili sınıflandırmadır; varsayılan olarak Telegram'a
#   gönderilmez.
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

STRONG_THRESHOLD = int(os.getenv("STRONG_THRESHOLD", "86"))
CANDIDATE_THRESHOLD = int(os.getenv("CANDIDATE_THRESHOLD", "78"))

# Çok fazla coin gönderilmesin.
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))

DB_PATH = os.getenv("STATE_DB_PATH", "balina_v14.db")
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
log = logging.getLogger("balina-v14")


# ============================================================
# HTTP
# ============================================================

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
        retry = Retry(allowed_methods=["GET", "POST"], **kw)
    except TypeError:
        retry = Retry(method_whitelist=["GET", "POST"], **kw)

    s = requests.Session()
    s.mount(
        "https://",
        HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=retry
        )
    )
    s.headers.update({"User-Agent": "BalinaRadari-V14/1.0"})
    return s


S = build_session()


def api(base, path, params=None):
    r = S.get(base + path, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def telegram(text):
    if not TOKEN or not CHAT:
        log.warning("Telegram bilgileri eksik.")
        return False

    try:
        r = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT,
                "text": text,
                "disable_web_page_preview": True
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
        log.debug(
            "%s %s kline: %s",
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
# HELPERS
# ============================================================

def pct(a, b):
    if a is None or b is None or a <= 0:
        return 0.0
    return (b - a) / a * 100.0


def clamp(v):
    return max(0, min(100, int(round(v))))


def avg(values):
    return sum(values) / len(values) if values else 0.0


def safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def rsi(closes, period=14):
    """
    Basit RSI. Harici kütüphane gerekmez.
    Yalnızca kapanışlardan hesaplanır.
    """
    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    gains = gains[-period:]
    losses = losses[-period:]

    ag = avg(gains)
    al = avg(losses)

    if al == 0:
        return 100.0

    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


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
                    score REAL
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

    def get_oi(self, symbol):
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute(
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

        with self.lock, sqlite3.connect(self.path) as db:
            db.execute(
                """
                INSERT INTO oi(symbol,value,ts)
                VALUES(?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
                """,
                (symbol, value, time.time())
            )

    def cooldown(self, symbol):
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT sent FROM state WHERE symbol=?",
                (symbol,)
            ).fetchone()

        return bool(
            row and
            time.time() - row[0] < COOLDOWN
        )

    def sent(self, symbol, score):
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute(
                """
                INSERT INTO state(symbol,sent,score)
                VALUES(?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score
                """,
                (symbol, time.time(), score)
            )


DBS = DB(DB_PATH)


# ============================================================
# CANDIDATES
# ============================================================

def candidates(spot, futures):
    fm = {x.get("symbol"): x for x in futures}
    result = []

    for item in spot:
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
                "BEARUSDT"
            )
        ):
            continue

        future = fm.get(symbol)
        if not future:
            continue

        try:
            spot_vol = float(item.get("quoteVolume", 0))
            fut_vol = float(future.get("quoteVolume", 0))
            daily_change = float(
                item.get("priceChangePercent", 0)
            )

            if spot_vol < MIN_VOLUME:
                continue

            if fut_vol < MIN_VOLUME:
                continue

            # Son 24 saatte zaten çok kaçmış coinleri
            # aday havuzundan çıkar.
            if daily_change > 16.0:
                continue

            result.append(symbol)

        except (TypeError, ValueError):
            continue

    return result


# ============================================================
# ANALİZ
# ============================================================

def analyze(symbol):
    try:
        # 1m: kısa vadeli yapı
        # 5m: daha geniş dip/birikim bağlamı
        spot_1m = klines(SPOT, symbol, "1m", 60)
        futures_1m = klines(FUT, symbol, "1m", 36)
        spot_5m = klines(SPOT, symbol, "5m", 24)

        if (
            len(spot_1m) < 45
            or len(futures_1m) < 30
            or len(spot_5m) < 18
        ):
            return {"status": "insufficient"}

        # ----------------------------------------------------
        # CANLI FİYAT
        # ----------------------------------------------------
        live = spot_1m[-1]

        price = safe_float(live[4])
        live_open = safe_float(live[1])
        live_high = safe_float(live[2])
        live_low = safe_float(live[3])

        live_change = pct(live_open, price)

        closes_1m = [
            safe_float(x[4]) for x in spot_1m
        ]

        closes_5m = [
            safe_float(x[4]) for x in spot_5m
        ]

        m5 = pct(closes_5m[-2], price)
        m15 = pct(closes_5m[-4], price)
        m30 = pct(closes_5m[-7], price)

        # ----------------------------------------------------
        # GEÇ KALMA
        # ----------------------------------------------------
        # Amaç pump kovalamak değil.
        if live_change > 1.20:
            return {"status": "late"}

        if m5 > 2.50 or m15 > 4.50 or m30 > 7.0:
            return {"status": "late"}

        if live_change < -2.0 or m5 < -3.5:
            return {"status": "weak"}

        # ----------------------------------------------------
        # YEREL DİP / KONUM
        # ----------------------------------------------------
        # Son 30 adet tamamlanmış 1m mumun fiyat aralığı.
        # Fiyat bu aralığın alt bölümündeyse "dip bölgesi"
        # puanı yükselir.
        closed_1m = spot_1m[:-1]

        recent_lows = [
            safe_float(x[3])
            for x in closed_1m[-30:]
        ]

        recent_highs = [
            safe_float(x[2])
            for x in closed_1m[-30:]
        ]

        range_low = min(recent_lows)
        range_high = max(recent_highs)

        if range_high > range_low:
            location = (
                (price - range_low)
                / (range_high - range_low)
            ) * 100.0
        else:
            location = 50.0

        # 0 = aralığın dibi, 100 = tepesi.
        near_local_low = location <= 35
        very_near_low = location <= 20

        # ----------------------------------------------------
        # 5m DİP KONUMU
        # ----------------------------------------------------
        closed_5m = spot_5m[:-1]

        lows_5m = [
            safe_float(x[3])
            for x in closed_5m[-12:]
        ]

        highs_5m = [
            safe_float(x[2])
            for x in closed_5m[-12:]
        ]

        low5 = min(lows_5m)
        high5 = max(highs_5m)

        if high5 > low5:
            location5 = (
                (price - low5)
                / (high5 - low5)
            ) * 100.0
        else:
            location5 = 50.0

        near_5m_low = location5 <= 40
        very_near_5m_low = location5 <= 25

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------
        rsi_1m = rsi(closes_1m[-30:], 14)
        rsi_5m = rsi(closes_5m[-18:], 14)

        # Çok düşük RSI tek başına al sinyali değildir.
        # Ama dip bölgesinde + dönüş ile birlikte destekleyicidir.
        rsi_recovery = (
            rsi_1m >= 38
            and rsi_1m <= 62
        )

        rsi_oversold_recovery = (
            rsi_1m < 42
            and rsi_5m < 48
        )

        # ----------------------------------------------------
        # PRICE ACTION
        # ----------------------------------------------------
        a = spot_1m[-2]
        b = spot_1m[-3]
        c = spot_1m[-4]

        a_low = safe_float(a[3])
        b_low = safe_float(b[3])
        c_low = safe_float(c[3])

        a_high = safe_float(a[2])
        b_high = safe_float(b[2])

        a_close = safe_float(a[4])
        b_close = safe_float(b[4])

        a_open = safe_float(a[1])

        # Higher-Low daha sıkı:
        higher_low = (
            a_low > b_low
            and a_low >= c_low
            and live_low >= a_low
        )

        break_prev_high = price > a_high
        break_two_high = price > max(a_high, b_high)

        reclaim = (
            a_close >= b_close
            and live_change >= -0.05
        )

        # Son mum gövdesi pozitifse destek.
        candle_reclaim = a_close >= a_open

        reversal_points = sum([
            higher_low,
            break_prev_high,
            reclaim,
            candle_reclaim
        ])

        reversal = (
            reversal_points >= 2
            or break_two_high
        )

        # ----------------------------------------------------
        # ALT GÖLGE / SATIŞIN KARŞILANMASI
        # ----------------------------------------------------
        body = abs(a_close - a_open)
        lower_wick = (
            min(a_open, a_close) - safe_float(a[3])
        )

        wick_rejection = (
            lower_wick > 0
            and lower_wick >= body * 0.8
        )

        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------
        buy3 = sum(
            safe_float(x[10])
            for x in spot_1m[-3:]
        )

        vol3 = sum(
            safe_float(x[7])
            for x in spot_1m[-3:]
        )

        buy5 = sum(
            safe_float(x[10])
            for x in spot_1m[-5:]
        )

        vol5 = sum(
            safe_float(x[7])
            for x in spot_1m[-5:]
        )

        bp3 = (
            buy3 / vol3 * 100.0
            if vol3 > 0 else 50.0
        )

        bp5 = (
            buy5 / vol5 * 100.0
            if vol5 > 0 else 50.0
        )

        buyer_pressure = (
            bp3 * 0.65
            + bp5 * 0.35
        )

        # ----------------------------------------------------
        # HACİM
        # ----------------------------------------------------
        futures_closed = futures_1m[:-1]

        spot_volumes = [
            safe_float(x[7])
            for x in spot_1m
        ]

        futures_volumes = [
            safe_float(x[7])
            for x in futures_1m
        ]

        trade_counts = [
            safe_float(x[8])
            for x in spot_1m
        ]

        avg_spot = avg([
            safe_float(x[7])
            for x in closed_1m[-30:]
        ])

        avg_futures = avg([
            safe_float(x[7])
            for x in futures_closed[-30:]
        ])

        avg_trades = avg([
            safe_float(x[8])
            for x in closed_1m[-30:]
        ])

        if min(
            avg_spot,
            avg_futures,
            avg_trades
        ) <= 0:
            return {"status": "insufficient"}

        recent_spot = avg(
            spot_volumes[-3:]
        )

        recent_futures = avg(
            futures_volumes[-3:]
        )

        recent_trades = avg(
            trade_counts[-3:]
        )

        spot_ratio = recent_spot / avg_spot
        futures_ratio = recent_futures / avg_futures
        trade_ratio = recent_trades / avg_trades

        previous_spot = avg(
            spot_volumes[-6:-3]
        )

        volume_accel = (
            recent_spot / previous_spot
            if previous_spot > 0 else 1.0
        )

        # ----------------------------------------------------
        # SPOT ÖNCÜLÜĞÜ
        # ----------------------------------------------------
        spot_leads = (
            spot_ratio >= 2.0
            and spot_ratio >= futures_ratio * 1.15
        )

        spot_flow = spot_ratio >= 2.0
        trade_flow = trade_ratio >= 1.15

        # Futures düşük diye kaliteli spot hareketini
        # otomatik olarak çöpe atmıyoruz.
        flow_good = (
            spot_flow
            and buyer_pressure >= 65
        )

        # ----------------------------------------------------
        # DİP/BİRİKİM PUANI — 30
        # ----------------------------------------------------
        dip_score = 0

        if very_near_low:
            dip_score += 20
        elif near_local_low:
            dip_score += 14
        elif location <= 50:
            dip_score += 7

        if very_near_5m_low:
            dip_score += 10
        elif near_5m_low:
            dip_score += 6

        # ----------------------------------------------------
        # FİYAT DÖNÜŞÜ — 25
        # ----------------------------------------------------
        reversal_score = 0

        if higher_low:
            reversal_score += 8

        if break_prev_high:
            reversal_score += 8

        if break_two_high:
            reversal_score += 4

        if reclaim:
            reversal_score += 5

        if wick_rejection:
            reversal_score += 4

        reversal_score = min(25, reversal_score)

        # ----------------------------------------------------
        # SPOT PARA GİRİŞİ — 20
        # ----------------------------------------------------
        flow_score = 0

        if spot_ratio >= 4:
            flow_score += 12
        elif spot_ratio >= 3:
            flow_score += 10
        elif spot_ratio >= 2.5:
            flow_score += 8
        elif spot_ratio >= 2:
            flow_score += 6

        if spot_leads:
            flow_score += 5

        if volume_accel >= 2:
            flow_score += 3

        flow_score = min(20, flow_score)

        # ----------------------------------------------------
        # ALICI BASKISI — 15
        # ----------------------------------------------------
        buyer_score = 0

        if buyer_pressure >= 85:
            buyer_score += 15
        elif buyer_pressure >= 78:
            buyer_score += 12
        elif buyer_pressure >= 70:
            buyer_score += 9
        elif buyer_pressure >= 65:
            buyer_score += 6

        # ----------------------------------------------------
        # TEYİT — 10
        # ----------------------------------------------
