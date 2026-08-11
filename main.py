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
# ============================================================
# ADAY FİLTRESİ
# ============================================================

def candidates(spot, futures):
    fmap = {x.get("symbol"): x for x in futures}
    out = []

    for x in spot:
        symbol = x.get("symbol", "")

        if (
            not symbol.endswith("USDT")
            or symbol in EXCLUDED
            or any(
                symbol.endswith(z)
                for z in (
                    "UPUSDT",
                    "DOWNUSDT",
                    "BULLUSDT",
                    "BEARUSDT"
                )
            )
        ):
            continue

        f = fmap.get(symbol)

        if not f:
            continue

        try:
            spot_vol = float(x.get("quoteVolume", 0))
            fut_vol = float(f.get("quoteVolume", 0))
            daily_change = float(
                x.get("priceChangePercent", 0)
            )

            if spot_vol < MIN_VOLUME:
                continue

            if fut_vol < MIN_VOLUME:
                continue

            if daily_change > 16.0:
                continue

            out.append(symbol)

        except (TypeError, ValueError):
            continue

    return out


# ============================================================
# ANALİZ MOTORU
# ============================================================

def analyze(symbol):
    try:
        sp = klines(
            SPOT,
            symbol,
            "1m",
            48
        )

        fu = klines(
            FUT,
            symbol,
            "1m",
            36
        )

        sp5 = klines(
            SPOT,
            symbol,
            "5m",
            18
        )

        if (
            len(sp) < 35
            or len(fu) < 30
            or len(sp5) < 10
        ):
            return {"status": "insufficient"}

        live = sp[-1]

        price = float(live[4])

        lc = pct(
            float(live[1]),
            price
        )

        c5 = [
            float(x[4])
            for x in sp5
        ]

        m5 = pct(
            c5[-2],
            price
        )

        m15 = pct(
            c5[-4],
            price
        )

        m30 = pct(
            c5[-7],
            price
        )

        # ----------------------------------------------------
        # GEÇ KALMA / AŞIRI DÜŞÜŞ FİLTRESİ
        # ----------------------------------------------------

        if (
            lc > 1.20
            or m5 > 2.50
            or m15 > 4.50
            or m30 > 7.0
        ):
            return {"status": "late"}

        if (
            lc < -2.0
            or m5 < -3.5
        ):
            return {"status": "weak"}

        # ----------------------------------------------------
        # MA SIKIŞMASI
        # ----------------------------------------------------

        closes = [
            float(x[4])
            for x in sp
        ]

        ma7 = average(
            closes[-7:]
        )

        ma30 = average(
            closes[-30:]
        )

        ma_diff_pct = (
            abs(ma7 - ma30)
            / price
            * 100.0
        )

        ma_squeeze = (
            ma_diff_pct <= 0.85
        )

        ma_rising = (
            ma7 > average(
                closes[-10:-3]
            )
        )

        # ----------------------------------------------------
        # YEREL DİP KONUMU
        # ----------------------------------------------------

        closed = sp[:-1]

        lows = [
            float(x[3])
            for x in closed[-30:]
        ]

        highs = [
            float(x[2])
            for x in closed[-30:]
        ]

        lo = min(lows)
        hi = max(highs)

        location = (
            (price - lo)
            / (hi - lo)
            * 100.0
            if hi > lo
            else 50.0
        )

        very_low = (
            location <= 25.0
        )

        near_low = (
            location <= 40.0
        )

        # ----------------------------------------------------
        # DİP TABANI
        # ----------------------------------------------------

        last3_lows = [
            float(x[3])
            for x in closed[-3:]
        ]

        base_low = min(
            last3_lows
        )

        base_high = max(
            last3_lows
        )

        base_spread = (
            (base_high - base_low)
            / price
            * 100.0
        )

        base_forming = (
            base_spread <= 0.45
            and price >= base_low * 0.998
        )

        # ----------------------------------------------------
        # PRICE ACTION
        # ----------------------------------------------------

        a = sp[-2]
        b = sp[-3]

        a_open = float(a[1])
        a_high = float(a[2])
        a_low = float(a[3])
        a_close = float(a[4])

        b_low = float(b[3])

        higher_low = (
            a_low > b_low
            and float(live[3]) >= a_low
        )

        break_high = (
            price > a_high
        )

        body = abs(
            a_close - a_open
        )

        lower_wick = (
            min(a_open, a_close)
            - a_low
        )

        wick_rejection = (
            lower_wick > 0
            and lower_wick >= max(
                body * 0.8,
                price * 0.0003
            )
        )

        reversal = (
            higher_low
            or break_high
            or wick_rejection
        )

        # ----------------------------------------------------
        # HACİM / PARA AKIŞI
        # ----------------------------------------------------

        sc = sp[:-1]
        fc = fu[:-1]

        sv = [
            float(x[7])
            for x in sp
        ]

        fv = [
            float(x[7])
            for x in fu
        ]

        tr = [
            float(x[8])
            for x in sp
        ]

        avs = average([
            float(x[7])
            for x in sc[-18:]
        ])

        avf = average([
            float(x[7])
            for x in fc[-18:]
        ])

        avt = average([
            float(x[8])
            for x in sc[-18:]
        ])

        if min(
            avs,
            avf,
            avt
        ) <= 0:
            return {
                "status": "insufficient"
            }

        sr = (
            average(sv[-3:])
            / avs
        )

        fr = (
            average(fv[-3:])
            / avf
        )

        trr = (
            average(tr[-3:])
            / avt
        )

        prev_vol = average(
            sv[-6:-3]
        )

        vol_acc = (
            average(sv[-3:])
            / prev_vol
            if prev_vol > 0
            else 1.0
        )

        # ----------------------------------------------------
        # SESSİZ BİRİKİM
        # ----------------------------------------------------

        silent_accum = (
            -0.35 <= lc <= 0.45
            and (
                sr >= 2.2
                or fr >= 2.2
            )
        )

        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------

        buy3 = sum(
            float(x[10])
            for x in sp[-3:]
        )

        vol3 = sum(
            float(x[7])
            for x in sp[-3:]
        )

        bp3 = (
            buy3 / vol3 * 100.0
            if vol3 > 0
            else 50.0
        )

        buy5 = sum(
            float(x[10])
            for x in sp[-5:]
        )

        vol5 = sum(
            float(x[7])
            for x in sp[-5:]
        )

        bp5 = (
            buy5 / vol5 * 100.0
            if vol5 > 0
            else 50.0
        )

        bp = (
            bp3 * 0.65
            + bp5 * 0.35
        )

        spot_leads = (
            sr >= 1.8
            and sr >= fr * 1.05
        )

        # ----------------------------------------------------
        # VOLATİLİTE DARALMASI
        # ----------------------------------------------------

        recent_ranges = [
            (
                float(x[2])
                - float(x[3])
            )
            / float(x[4])
            * 100.0
            for x in sp[-6:]
            if float(x[4]) > 0
        ]

        old_ranges = [
            (
                float(x[2])
                - float(x[3])
            )
            / float(x[4])
            * 100.0
            for x in sp[-18:-6]
            if float(x[4]) > 0
        ]

        recent_range = average(
            recent_ranges
        )

        old_range = average(
            old_ranges
        )

        volatility_compression = (
            old_range > 0
            and recent_range
            <= old_range * 0.75
        )

        # ----------------------------------------------------
        # KIRILIMA MESAFE
        # ----------------------------------------------------

        resistance = max(
            float(x[2])
            for x in closed[-5:]
        )

        breakout_distance = (
            max(
                0.0,
                (resistance - price)
                / price
                * 100.0
            )
            if price > 0
            else 99.0
        )

        close_to_breakout = (
            0.0
            <= breakout_distance
            <= 0.60
        )

        # ----------------------------------------------------
        # NEGATİF YAPILAR
        # ----------------------------------------------------

        falling = (
            m5 < -0.8
            and m15 < -1.2
            and not reversal
        )

        distribution = (
            bp < 58.0
            and sr >= 3.0
            and m5 < -0.3
        )

        # ----------------------------------------------------
        # PUAN
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
            score += 15
            reasons.append(
                f"📐 MA sıkışması (%{ma_diff_pct:.2f})"
            )

        if ma_rising:
            score += 5
            reasons.append(
                "📈 Kısa MA yukarı dönüyor"
            )

        if silent_accum:
            score += 15
            reasons.append(
                "🤫 Sessiz birikim tespit edildi"
            )

        if volatility_compression:
            score += 6
            reasons.append(
                "🤏 Volatilite daralıyor"
            )

        if higher_low:
            score += 7
            reasons.append(
                "📐 Higher-Low oluştu"
            )

        if break_high:
            score += 8
            reasons.append(
                "💥 Önceki tepe kırıldı"
            )

        if wick_rejection:
            score += 5
            reasons.append(
                "🛡️ Dipte satış reddedildi"
            )

        if sr >= 3.5:
            score += 12
            reasons.append(
                f"🐋 Spot akışı çok güçlü ({sr:.2f}x)"
            )
        elif sr >= 2.0:
            score += 8
            reasons.append(
                f"📈 Spot akışı başladı ({sr:.2f}x)"
            )

        if fr >= 1.8:
            score += 7
            reasons.append(
                f"⚡ Futures destekliyor ({fr:.2f}x)"
            )

        if trr >= 1.8:
            score += 7
            reasons.append(
                f"🤖 İşlem sayısı arttı ({trr:.2f}x)"
            )

        if bp >= 80:
            score += 12
            reasons.append(
                f"🐋 Çok güçlü alıcı baskısı (%{bp:.1f})"
            )
        elif bp >= 70:
            score += 9
            reasons.append(
                f"🟢 Güçlü alıcı baskısı (%{bp:.1f})"
            )
        elif bp >= 62:
            score += 5
            reasons.append(
                f"🟢 Pozitif alıcı akışı (%{bp:.1f})"
            )

        if spot_leads:
            score += 5
            reasons.append(
                "🐋 Spot akışı öncülük ediyor"
            )

        if close_to_breakout:
            score += 6
            reasons.append(
                f"🎯 Kırılıma çok yakın (%{breakout_distance:.2f})"
            )

        if vol_acc >= 2.0:
            score += 6
            reasons.append(
                f"🚀 Hacim ivmesi güçlü ({vol_acc:.2f}x)"
            )
        elif vol_acc >= 1.3:
            score += 4
            reasons.append(
                f"🔥 Hacim ivmesi artıyor ({vol_acc:.2f}x)"
            )

        if falling:
            score -= 15
            reasons.append(
                "⚠️ Düşüş yapısı devam ediyor"
            )

        if distribution:
            score -= 20
            reasons.append(
                "⚠️ Dağıtım riski"
            )

        score = clamp(score)

        # ----------------------------------------------------
        # OI
        # ----------------------------------------------------

        oi_change = None

        if score >= STRONG_THRESHOLD - 5:
            now_oi = open_interest(symbol)
            old_oi = DBS.get_oi(symbol)

            if (
                old_oi is not None
                and now_oi is not None
            ):
                oi_change = pct(
                    old_oi,
                    now_oi
                )

                if oi_change >= 0.8:
                    score = clamp(
                        score + 4
                    )
                    reasons.append(
                        f"📈 OI destekli (+%{oi_change:.2f})"
                    )

                elif oi_change <= -1.5:
                    score = clamp(
                        score - 4
                    )
                    reasons.append(
                        f"⚠️ OI geriliyor (%{oi_change:.2f})"
                    )

            DBS.put_oi(
                symbol,
                now_oi
            )

        # ----------------------------------------------------
        # SİNYAL AŞAMALARI
        # ----------------------------------------------------

        preparation = (
            (very_low or near_low)
            and (
                base_forming
                or ma_squeeze
                or silent_accum
            )
            and bp >= 60
            and not falling
            and not distribution
            and lc <= 0.80
            and m5 <= 1.40
        )

        strong = (
            preparation
            and score >= STRONG_THRESHOLD
            and bp >= 68
            and (
                higher_low
                or wick_rejection
                or ma_rising
            )
            and (
                spot_leads
                or sr >= 2.5
                or silent_accum
            )
        )

        rocket = (
            strong
            and score >= ROCKET_THRESHOLD
            and bp >= 75
            and vol_acc >= 1.25
            and (
                break_high
                or (
                    close_to_breakout
                    and reversal
                    and ma_rising
                )
            )
            and m5 <= 1.80
            and lc <= 1.00
        )

        if rocket:
            status = "ROCKET"
            stage = 3
            signal_type = "🚀 ÇOK ÇOK GÜÇLÜ AL"

        elif strong:
            status = "STRONG"
            stage = 2
            signal_type = "🟢 GÜÇLÜ AL"

        elif preparation:
            status = "PREP"
            stage = 1
            signal_type = "🔵 HAZIRLIK AL"

        else:
            status = "PASS"
            stage = 0
            signal_type = "⚪ PASS"

        return {
            "status": status,
            "stage": stage,
            "type": signal_type,
            "symbol": symbol,
            "score": score,
            "price": price,
            "location": location,
            "base_forming": base_forming,
            "sr": sr,
            "fr": fr,
            "trr": trr,
            "bp": bp,
            "lc": lc,
            "m5": m5,
            "m15": m15,
            "ma_squeeze": ma_squeeze,
            "ma_diff_pct": ma_diff_pct,
            "silent_accum": silent_accum,
            "volatility_compression": volatility_compression,
            "breakout_distance": breakout_distance,
            "vol_acc": vol_acc,
            "higher_low": higher_low,
            "break_high": break_high,
            "wick_rejection": wick_rejection,
            "spot_leads": spot_leads,
            "oi": oi_change,
            "reasons": reasons
        }

    except Exception as e:
        log.exception(
            "%s analiz hatası: %s",
            symbol,
            e
        )

        return {
            "status": "error"
                    }
# ============================================================
# TELEGRAM MESAJI
# ============================================================

def message(r):
    oi_text = (
        "veri bekleniyor"
        if r["oi"] is None
        else f"%{r['oi']:.2f}"
    )

    if r["status"] == "ROCKET":
        header = "🐋 BALİNA RADARI V16 — 🚀 ÇOK ÇOK GÜÇLÜ AL"
        footer = (
            "🚀 Kırılım tetiklenmiş durumda. "
            "Dip + para akışı + momentum aynı yönde."
        )

    elif r["status"] == "STRONG":
        header = "🐋 BALİNA RADARI V16 — 🟢 GÜÇLÜ AL"
        footer = (
            "🟢 Aradığımız yapı güçlendi. "
            "Hazırlık aşamasından güçlü teyide geçti."
        )

    else:
        header = "🐋 BALİNA RADARI V16 — 🔵 HAZIRLIK AL"
        footer = (
            "👁️ Dip/birikim yapısı oluşuyor. "
            "Güçlü teyit henüz tamamlanmadı."
        )

    def check(value):
        return "✅" if value else "❌"

    reasons = "\n".join(
        f"• {x}"
        for x in r["reasons"]
    )

    return (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🪙 #{r['symbol']}\n"
        f"💰 Fiyat: {r['price']:.8g}\n"
        f"🏆 GÜÇ SKORU: {r['score']}/100\n\n"

        "📍 DİP KONUMU\n"
        f"• Son 30m konumu: %{r['location']:.1f}\n"
        f"• Dip tabanı: "
        f"{check(r['base_forming'])}\n\n"

        "🐋 PARA AKIŞI\n"
        f"• Spot hacim: {r['sr']:.2f}x\n"
        f"• Futures hacim: {r['fr']:.2f}x\n"
        f"• İşlem sayısı: {r['trr']:.2f}x\n"
        f"• Alıcı baskısı: %{r['bp']:.1f}\n"
        f"• Spot öncü: "
        f"{check(r['spot_leads'])}\n\n"

        "📐 KIRILIM HAZIRLIĞI\n"
        f"• MA sıkışması: "
        f"{check(r['ma_squeeze'])} "
        f"(%{r['ma_diff_pct']:.2f})\n"
        f"• Sessiz birikim: "
        f"{check(r['silent_accum'])}\n"
        f"• Volatilite daralması: "
        f"{check(r['volatility_compression'])}\n"
        f"• Kırılıma mesafe: "
        f"%{r['breakout_distance']:.2f}\n"
        f"• Hacim ivmesi: "
        f"{r['vol_acc']:.2f}x\n\n"

        "📈 PRICE ACTION\n"
        f"• Higher-Low: "
        f"{check(r['higher_low'])}\n"
        f"• Tepe kırılımı: "
        f"{check(r['break_high'])}\n"
        f"• Satış reddi: "
        f"{check(r['wick_rejection'])}\n\n"

        "⚡ MOMENTUM\n"
        f"• Canlı 1m: {r['lc']:+.2f}%\n"
        f"• 5m: {r['m5']:+.2f}%\n"
        f"• 15m: {r['m15']:+.2f}%\n"
        f"• OI: {oi_text}\n\n"

        "🔎 NEDEN SİNYAL?\n"
        f"{reasons}\n\n"

        f"{footer}\n"
        "⚠️ Teknik filtredir; risk yönetimi sana aittir."
    )


# ============================================================
# TARAMA
# ============================================================

def scan():
    start = time.time()

    spot = tickers(SPOT)
    futures = tickers(FUT)

    if not spot or not futures:
        log.warning(
            "Ticker verisi alınamadı."
        )
        return True

    symbols = candidates(
        spot,
        futures
    )

    signals = []
    stats = {}

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as executor:

        jobs = [
            executor.submit(
                analyze,
                symbol
            )
            for symbol in symbols
        ]

        for job in as_completed(jobs):
            result = job.result()

            status = result.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0) + 1
            )

            if status in (
                "PREP",
                "STRONG",
                "ROCKET"
            ):
                signals.append(result)

    # Öncelik:
    # 🚀 ROCKET
    # 🟢 STRONG
    # 🔵 PREP

    signals.sort(
        key=lambda x: (
            x.get("stage", 0),
            x.get("score", 0)
        ),
        reverse=True
    )

    sent = 0

    for result in signals[:MAX_SIGNALS]:

        symbol = result["symbol"]

        if DBS.cooldown(symbol):
            continue

        if telegram(
            message(result)
        ):
            DBS.sent(
                symbol,
                result["score"]
            )
            sent += 1

        time.sleep(0.5)

    elapsed = time.time() - start

    errors = stats.get(
        "error",
        0
    )

    total = max(
        1,
        len(symbols)
    )

    log.info(
        "🐋 V16 | Aday:%d | "
        "HAZIRLIK:%d | GÜÇLÜ:%d | "
        "ÇOK GÜÇLÜ:%d | Geç:%d | "
        "Hata:%d | Gönder:%d | %.1fs",

        len(symbols),

        stats.get(
            "PREP",
            0
        ),

        stats.get(
            "STRONG",
            0
        ),

        stats.get(
            "ROCKET",
            0
        ),

        stats.get(
            "late",
            0
        ),

        errors,

        sent,

        elapsed
    )

    return (
        errors / total > 0.30
        or elapsed >
        SCAN_INTERVAL * 1.25
    )


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return (
        "🐋 Balina Radarı V16 "
        "Bottom Launcher Aktif!"
    )


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V16",
        "strong_threshold":
            STRONG_THRESHOLD,
        "rocket_threshold":
            ROCKET_THRESHOLD,
        "candidate_threshold":
            CANDIDATE_THRESHOLD
    }


# ============================================================
# ANA DÖNGÜ
# ============================================================

def loop():

    log.info(
        "🐋 BALİNA RADARI V16 başlatılıyor..."
    )

    if TOKEN and CHAT:

        telegram(
            "🐋 BALİNA RADARI V16 AKTİF\n\n"
            "🔵 HAZIRLIK AL\n"
            "🟢 GÜÇLÜ AL\n"
            "🚀 ÇOK ÇOK GÜÇLÜ AL\n\n"
            "🟦 Yerel dip tespiti\n"
            "🧱 Dip tabanı analizi\n"
            "🤫 Sessiz birikim\n"
            "📐 MA sıkışması\n"
            "🤏 Volatilite daralması\n"
            "🐋 Spot para akışı\n"
            "🎯 Kırılım mesafesi\n"
            "📈 Price Action teyidi\n"
            "🛡️ Geç kalma filtresi"
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
            - started
        )

        if backoff:

            wait = max(
                180,
                SCAN_INTERVAL * 3
            )

            log.warning(
                "🛑 Koruma beklemesi: "
                "%d saniye",
                wait
            )

            time.sleep(wait)

        else:

            time.sleep(
                max(
                    1,
                    SCAN_INTERVAL
                    - elapsed
                )
            )


# ============================================================
# BAŞLAT
# ============================================================

Thread(
    target=loop,
    daemon=True,
    name="balina-v16"
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
