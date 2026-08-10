
import os
import time
import sqlite3
import logging
from dataclasses import dataclass
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI — AMIN V8
# EARLY LONG + SHORT / VOLUME + OI + MOMENTUM + BREAKOUT
# ============================================================

@dataclass(frozen=True)
class Config:
    min_volume: float = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
    scan_interval: int = int(os.getenv("SCAN_INTERVAL", "300"))
    workers: int = int(os.getenv("MAX_WORKERS", "8"))
    signal_score: int = int(os.getenv("SIGNAL_SCORE", "78"))
    max_signals: int = int(os.getenv("MAX_SIGNALS_PER_SCAN", "3"))
    cooldown: int = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
    timeout: int = int(os.getenv("REQUEST_TIMEOUT", "10"))
    db: str = os.getenv("STATE_DB_PATH", "balina_v8.db")


CFG = Config()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {
    "BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT",
    "TUSDUSDT", "USDPUSDT", "DAIUSDT"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("balina-v8")


# ============================================================
# HTTP
# ============================================================

def build_session():
    s = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        pool_connections=32,
        pool_maxsize=32,
        max_retries=retry,
    )

    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "BalinaRadari-AMIN-V8/1.0"})

    return s


S = build_session()


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V8 Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V8",
        "score": CFG.signal_score
    }


# ============================================================
# API
# ============================================================

def api(base, path, params=None):
    r = S.get(
        base + path,
        params=params,
        timeout=CFG.timeout
    )
    r.raise_for_status()
    return r.json()


def telegram(text):
    if not TOKEN or not CHAT:
        log.warning("Telegram token/chat ID eksik.")
        return False

    try:
        r = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=CFG.timeout,
        )

        r.raise_for_status()
        return bool(r.json().get("ok"))

    except Exception as e:
        log.error("Telegram hatası: %s", e)
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
        log.error("Ticker hatası: %s", e)
        return []


def klines(base, symbol, interval, limit=120):
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
            }
        )

    except Exception as e:
        log.debug("%s %s kline: %s", symbol, interval, e)
        return []


def open_interest(symbol):
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


def funding(symbol):
    try:
        return float(
            api(
                FUT,
                "/fapi/v1/premiumIndex",
                {"symbol": symbol}
            )["lastFundingRate"]
        )
    except Exception:
        return 0.0


# ============================================================
# MATH
# ============================================================

def pct(a, b):
    if not a or a <= 0 or b is None:
        return 0.0
    return ((b - a) / a) * 100


def clamp(v):
    return max(0, min(100, int(v)))


def ema(values, period):
    if len(values) < period:
        return None

    k = 2 / (period + 1)
    value = sum(values[:period]) / period

    for x in values[period:]:
        value = (x - value) * k + value

    return value


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((period - 1) * avg_gain + gains[i]) / period
        avg_loss = ((period - 1) * avg_loss + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(values):
    if len(values) < 40:
        return None, None, None

    fast = []
    slow = []

    k12 = 2 / 13
    k26 = 2 / 27

    e12 = sum(values[:12]) / 12
    e26 = sum(values[:26]) / 26

    fast.append(e12)

    for x in values[12:]:
        e12 = (x - e12) * k12 + e12
        fast.append(e12)

    slow = [e26]

    for x in values[26:]:
        e26 = (x - e26) * k26 + e26
        slow.append(e26)

    n = min(len(fast), len(slow))

    line = [
        fast[-n + i] - slow[-n + i]
        for i in range(n)
    ]

    if len(line) < 10:
        return None, None, None

    signal = sum(line[:9]) / 9
    k9 = 2 / 10

    for x in line[9:]:
        signal = (x - signal) * k9 + signal

    return line[-1], signal, line[-1] - signal


# ============================================================
# DATABASE
# ============================================================

class DB:

    def __init__(self, path):
        self.path = path
        self.lock = Lock()

        with self.lock, sqlite3.connect(path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL,
                    score REAL
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS oi(
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    ts REAL
                )
            """)

    def oi_reference(self, symbol):
        with self.lock, sqlite3.connect(self.path) as c:
            row = c.execute(
                "SELECT value,ts FROM oi WHERE symbol=?",
                (symbol,)
            ).fetchone()

        if not row:
            return None

        value, ts = row

        if time.time() - ts > CFG.scan_interval * 3:
            return None

        return float(value)

    def save_oi(self, symbol, value):
        if value is None:
            return

        with self.lock, sqlite3.connect(self.path) as c:
            c.execute("""
                INSERT INTO oi VALUES(?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
            """, (symbol, value, time.time()))

    def cooldown(self, symbol):
        with self.lock, sqlite3.connect(self.path) as c:
            row = c.execute(
                "SELECT sent FROM state WHERE symbol=?",
                (symbol,)
            ).fetchone()

        return bool(
            row and
            time.time() - row[0] < CFG.cooldown
        )

    def mark_sent(self, symbol, score):
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute("""
                INSERT INTO state VALUES(?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score
            """, (symbol, time.time(), score))


DBS = DB(CFG.db)


# ============================================================
# CANDIDATES
# ============================================================

def candidates(spot, futures):

    fmap = {
        x.get("symbol"): x
        for x in futures
    }

    result = []

    for x in spot:

        symbol = x.get("symbol", "")

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

        f = fmap.get(symbol)

        if not f:
            continue

        try:
            sv = float(x.get("quoteVolume", 0))
            fv = float(f.get("quoteVolume", 0))

            if sv < CFG.min_volume:
                continue

            if fv < CFG.min_volume:
                continue

        except (TypeError, ValueError):
            continue

        result.append(x)

    return result


# ============================================================
# ANALYSIS
# ============================================================

def analyze(ticker):

    symbol = ticker["symbol"]

    try:

        sp5 = klines(SPOT, symbol, "5m", 100)
        sp15 = klines(SPOT, symbol, "15m", 120)
        fu5 = klines(FUT, symbol, "5m", 100)

        if (
            len(sp5) < 70 or
            len(sp15) < 70 or
            len(fu5) < 70
        ):
            return {"status": "insufficient"}

        # Son açık olmayan mum kullanılmaz.
        s5 = sp5[:-1]
        s15 = sp15[:-1]
        f5 = fu5[:-1]

        close5 = [float(x[4]) for x in s5]
        close15 = [float(x[4]) for x in s15]

        high15 = [float(x[2]) for x in s15]
        low15 = [float(x[3]) for x in s15]

        vol5 = [float(x[7]) for x in s5]
        futvol5 = [float(x[7]) for x in f5]

        buy5 = [float(x[10]) for x in s5]

        price = float(sp5[-1][4])

        # ----------------------------------------------------
        # MOMENTUM
        # ----------------------------------------------------

        m5 = pct(close5[-2], close5[-1])
        m15 = pct(close15[-2], close15[-1])
        m30 = pct(close15[-3], close15[-1])
        m60 = pct(close15[-5], close15[-1])

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        avg_vol = sum(vol5[-25:-1]) / 24
        avg_fut = sum(futvol5[-25:-1]) / 24

        if avg_vol <= 0 or avg_fut <= 0:
            return {"status": "insufficient"}

        vr = vol5[-1] / avg_vol
        fvr = futvol5[-1] / avg_fut

        buy_pct = (
            buy5[-1] / vol5[-1] * 100
            if vol5[-1] > 0 else 50
        )

        # Volume acceleration
        old_volume = sum(vol5[-8:-4]) / 4
        new_volume = sum(vol5[-4:]) / 4

        volume_acc = pct(old_volume, new_volume)

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        ema20 = ema(close15, 20)
        ema50 = ema(close15, 50)

        if ema20 is None or ema50 is None:
            return {"status": "insufficient"}

        trend_long = price > ema20 and ema20 > ema50
        trend_short = price < ema20 and ema20 < ema50

        # ----------------------------------------------------
        # RSI / MACD
        # ----------------------------------------------------

        rsi_val = rsi(close15)

        macd_val, macd_sig, macd_hist = macd(close15)

        if rsi_val is None or macd_hist is None:
            return {"status": "insufficient"}

        # ----------------------------------------------------
        # BREAKOUT / BREAKDOWN
        # ----------------------------------------------------

        resistance = max(high15[-25:-2])
        support = min(low15[-25:-2])

        breakout = price > resistance
        breakdown = price < support

        # ----------------------------------------------------
        # OI
        # ----------------------------------------------------

        now_oi = open_interest(symbol)
        old_oi = DBS.oi_reference(symbol)

        oi_change = (
            pct(old_oi, now_oi)
            if old_oi is not None and now_oi is not None
            else 0.0
        )

        oi_available = (
            old_oi is not None and
            now_oi is not None
        )

        DBS.save_oi(symbol, now_oi)

        fund = funding(symbol)

        # ====================================================
        # LONG SCORE
        # ====================================================

        long_score = 0
        long_reasons = []

        # Erken hacim
        if vr >= 4:
            long_score += 20
            long_reasons.append("🚀 Spot hacmi 4x+")

        elif vr >= 3:
            long_score += 16
            long_reasons.append("🔥 Spot hacmi 3x+")

        elif vr >= 2:
            long_score += 10
            long_reasons.append("📈 Spot hacmi 2x+")

        # Futures
        if fvr >= 3:
            long_score += 12
            long_reasons.append("⚡ Futures hacmi 3x+")

        elif fvr >= 2:
            long_score += 8
            long_reasons.append("⚡ Futures hacmi 2x+")

        # Buy pressure
        if buy_pct >= 68:
            long_score += 18
            long_reasons.append(
                f"🐋 Alıcı baskısı çok güçlü (%{buy_pct:.1f})"
            )

        elif buy_pct >= 62:
            long_score += 13
            long_reasons.append(
                f"🟢 Güçlü alıcı baskısı (%{buy_pct:.1f})"
            )

        elif buy_pct >= 57:
            long_score += 8
            long_reasons.append(
                f"🟢 Pozitif alıcı baskısı (%{buy_pct:.1f})"
            )

        # Volume acceleration
        if volume_acc >= 100:
            long_score += 12
            long_reasons.append("🚀 Hacim ivmesi 100%+")

        elif volume_acc >= 50:
            long_score += 8
            long_reasons.append("🔥 Hacim ivmesi güçlü")

        elif volume_acc >= 25:
            long_score += 4
            long_reasons.append("📈 Hacim ivmesi pozitif")

        # Early momentum
        if 0.1 <= m5 <= 2.5:
            long_score += 7
            long_reasons.append("🎯 5m erken momentum")

        if 0.2 <= m15 <= 3.5:
            long_score += 7
            long_reasons.append("🎯 15m erken momentum")

        # Trend
        if trend_long:
            long_score += 10
            long_reasons.append("📈 EMA20 > EMA50 trendi")

        # MACD
        if macd_hist > 0:
            long_score += 8
            long_reasons.append("📊 MACD pozitif")

        # RSI
        if 48 <= rsi_val <= 68:
            long_score += 8
            long_reasons.append(
                f"📊 RSI uygun ({rsi_val:.1f})"
            )

        elif 68 < rsi_val <= 74:
            long_score += 3
            long_reasons.append(
                f"📊 RSI güçlü ({rsi_val:.1f})"
            )

        # OI
        if oi_change >= 8:
            long_score += 15
            long_reasons.append(
                f"🐋 OI güçlü artıyor (+%{oi_change:.1f})"
            )

        elif oi_change >= 4:
            long_score += 10
            long_reasons.append(
                f"📈 OI artıyor (+%{oi_change:.1f})"
            )

        elif oi_change >= 2:
            long_score += 5
            long_reasons.append(
                f"📊 OI yükseliyor (+%{oi_change:.1f})"
            )

        # Breakout
        if breakout:
            long_score += 15
            long_reasons.append(
                f"💥 Direnç kırılımı ({resistance:.6g})"
            )

        # Negative funding
        if fund < -0.0005:
            long_score += 6
            long_reasons.append(
                "⚡ Negatif funding / squeeze potansiyeli"
            )

        # ====================================================
        # SHORT SCORE
        # ====================================================

        short_score = 0
        short_reasons = []

        if vr >= 4:
            short_score += 20
            short_reasons.append("🔻 Spot hacmi 4x+")

        elif vr >= 3:
            short_score += 16
            short_reasons.append("🔻 Spot hacmi 3x+")

        elif vr >= 2:
            short_score += 10
            short_reasons.append("📉 Spot hacmi 2x+")

        if fvr >= 3:
            short_score += 12
            short_reasons.append("⚡ Futures hacmi 3x+")

        elif fvr >= 2:
            short_score += 8
            short_reasons.append("⚡ Futures hacmi 2x+")

        sell_pct = 100 - buy_pct

        if sell_pct >= 68:
            short_score += 18
            short_reasons.append(
                f"🐻 Satıcı baskısı çok güçlü (%{sell_pct:.1f})"
            )

        elif sell_pct >= 62:
            short_score += 13
            short_reasons.append(
                f"🔴 Güçlü satıcı baskısı (%{sell_pct:.1f})"
            )

        elif sell_pct >= 57:
            short_score += 8
            short_reasons.append(
                f"🔴 Pozitif satış baskısı (%{sell_pct:.1f})"
            )

        if volume_acc >= 100:
            short_score += 12
            short_reasons.append("💥 Hacim ivmesi 100%+")

        elif volume_acc >= 50:
            short_score += 8
            short_reasons.append("🔥 Hacim ivmesi güçlü")

        elif volume_acc >= 25:
            short_score += 4
            short_reasons.append("📉 Hacim ivmesi pozitif")

        if -2.5 <= m5 <= -0.1:
            short_score += 7
            short_reasons.append("🎯 5m erken düşüş")

        if -3.5 <= m15 <= -0.2:
            short_score += 7
            short_reasons.append("🎯 15m erken düşüş")

        if trend_short:
            short_score += 10
            short_reasons.append("📉 EMA20 < EMA50 trendi")

        if macd_hist < 0:
            short_score += 8
            short_reasons.append("📉 MACD negatif")

        if 32 <= rsi_val <= 52:
            short_score += 8
            short_reasons.append(
                f"📊 RSI düşüş bölgesinde ({rsi_val:.1f})"
            )

        elif 26 <= rsi_val < 32:
            short_score += 3
            short_reasons.append(
                f"⚠️ RSI aşırı satıma yaklaşıyor ({rsi_val:.1f})"
            )

        if oi_change >= 8:
            short_score += 15
            short_reasons.append(
                f"🐻 OI güçlü artıyor (+%{oi_change:.1f})"
            )

        elif oi_change >= 4:
            short_score += 10
            short_reasons.append(
                f"📉 OI artıyor (+%{oi_change:.1f})"
            )

        elif oi_change >= 2:
            short_score += 5
            short_reasons.append(
                f"📊 OI yükseliyor (+%{oi_change:.1f})"
            )

        if breakdown:
            short_score += 15
            short_reasons.append(
                f"💥 Destek kırılımı ({support:.6g})"
            )

        if fund > 0.001:
            short_score += 6
            short_reasons.append(
                "⚡ Pozitif funding / long squeeze potansiyeli"
            )

        # ====================================================
        # OVEREXTENSION FILTER
        # ====================================================

        # Hareket çok ilerlediyse sinyali düşür.
        if m15 > 8:
            long_score -= 12
            long_reasons.append("⏰ Long hareket fazla ilerledi")

        if m15 < -8:
            short_score -= 12
            short_reasons.append("⏰ Short hareket fazla ilerledi")

        if rsi_val > 80:
            long_score -= 15

        if rsi_val < 20:
            short_score -= 15

        long_score = clamp(long_score)
        short_score = clamp(short_score)

        # ====================================================
        # SIGNAL
        # ====================================================

        best = max(long_score, short_score)

        if best < CFG.signal_score:
            return {
                "status": "below_threshold",
                "score": best
            }

        if long_score >= short_score:

            return {
                "status": "signal",
                "symbol": symbol,
                "direction": "🟢 LONG",
                "score": long_score,
                "price": price,
                "support": support,
                "resistance": resistance,
                "vr": vr,
                "fvr": fvr,
                "buy_pct": buy_pct,
                "rsi": rsi_val,
                "oi": oi_change,
                "fund": fund,
                "m5": m5,
                "m15": m15,
                "m30": m30,
                "m60": m60,
                "evidence": long_reasons,
            }

        return {
            "status": "signal",
            "symbol": symbol,
            "direction": "🔴 SHORT",
            "score": short_score,
            "price": price,
            "support": support,
            "resistance": resistance,
            "vr": vr,
            "fvr": fvr,
            "buy_pct": buy_pct,
            "rsi": rsi_val,
            "oi": oi_change,
            "fund": fund,
            "m5": m5,
            "m15": m15,
            "m30": m30,
            "m60": m60,
            "evidence": short_reasons,
        }

    except Exception as e:
        log.exception("%s analiz hatası: %s", symbol, e)
        return {"status": "error"}


# ============================================================
# TELEGRAM
# ============================================================

def format_message(r):

    evidence = "\n".join(
        "• " + x
        for x in r["evidence"][:8]
    )

    return (
        "🐋 BALİNA RADARI V8\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 YÖN: {r['direction']}\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 Fiyat: {r['price']:.8g}\n"
        f"🏆 SCORE: {r['score']}/100\n\n"

        "📊 PİYASA\n"
        f"• Spot hacim: {r['vr']:.2f}x\n"
        f"• Futures hacim: {r['fvr']:.2f}x\n"
        f"• Alıcı baskısı: %{r['buy_pct']:.1f}\n"
        f"• RSI: {r['rsi']:.1f}\n"
        f"• OI: %{r['oi']:.2f}\n"
        f"• Funding: %{r['fund'] * 100:.4f}\n\n"

        "📈 MOMENTUM\n"
        f"• 5m: %{r['m5']:.2f}\n"
        f"• 15m: %{r['m15']:.2f}\n"
        f"• 30m: %{r['m30']:.2f}\n"
        f"• 60m: %{r['m60']:.2f}\n\n"

        "📐 SEVİYELER\n"
        f"• Destek: {r['support']:.8g}\n"
        f"• Direnç: {r['resistance']:.8g}\n\n"

        "🔎 TEYİTLER\n"
        f"{evidence}\n\n"

        "⚠️ Teknik sinyal filtresidir; yatırım garantisi değildir."
    )


# ============================================================
# SCAN
# ============================================================

def scan():

    spot = tickers(SPOT)
    futures = tickers(FUT)

    if not spot or not futures:
        log.error("Ticker verisi alınamadı.")
        return

    cs = candidates(spot, futures)

    log.info(
        "📋 %d aday V8 motoruna gönderiliyor...",
        len(cs)
    )

    signals = []
    stats = {}

    with ThreadPoolExecutor(
        max_workers=CFG.workers
    ) as executor:

        futures_list = [
            executor.submit(analyze, x)
            for x in cs
        ]

        for future in as_completed(futures_list):

            try:
                result = future.result()
            except Exception:
                result = {"status": "error"}

            status = result.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0) + 1
            )

            if status == "signal":
                signals.append(result)

    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    sent = 0

    for result in signals:

        if sent >= CFG.max_signals:
            break

        symbol = result["symbol"]

        if DBS.cooldown(symbol):
            continue

        if telegram(format_message(result)):

            DBS.mark_sent(
                symbol,
                result["score"]
            )

            sent += 1

        time.sleep(0.5)

    log.info(
        "📊 SONUÇ | Aday:%d | Sinyal:%d | "
        "Alt:%d | Veri:%d | Hata:%d",
        len(cs),
        sent,
        stats.get("below_threshold", 0),
        stats.get("insufficient", 0),
        stats.get("error", 0)
    )

    if signals:
        log.info(
            "🏆 TOP: %s",
            ", ".join(
                f"{x['symbol']}="
                f"{x['direction']}("
                f"{x['score']})"
                for x in signals[:10]
            )
        )
    else:
        log.info("🔕 Bu taramada güçlü sinyal yok.")


# ============================================================
# MAIN LOOP
# ============================================================

def loop():

    log.info("🐋 BALİNA RADARI V8 başlatılıyor...")

    telegram(
        "🐋 BALİNA RADARI V8 AKTİF\n\n"
        "🟢 Long + 🔴 Short\n"
        "🐋 Open Interest\n"
        "⚡ Funding\n"
        "📊 Spot + Futures hacim\n"
        "🎯 Erken momentum\n"
        "📈 EMA + MACD + RSI\n"
        "💥 Breakout / Breakdown\n"
        f"🏆 Sinyal eşiği: {CFG.signal_score}/100"
    )

    while True:

        started = time.time()

        try:
            scan()

        except Exception as e:
            log.exception(
                "Ana tarama hatası: %s",
                e
            )

        elapsed = time.time() - started

        time.sleep(
            max(
                1,
                CFG.scan_interval - elapsed
            )
        )


# ============================================================
# START
# ============================================================

Thread(
    target=loop,
    daemon=True,
    name="balina-v8"
).start()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", "8080")
        ),
        use_reloader=False
    )
