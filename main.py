
import os
import time
import sqlite3
import logging
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V10 — EARLY IGNITION
#
# AMAÇ:
# Yükselmiş coinleri kovalamak yerine yükselişin ilk aşamasını
# yakalamak.
#
# MOTOR:
# 1) 1m mikro hareket
# 2) 3 dakikalık hacim ivmesi
# 3) İşlem sayısı ivmesi
# 4) Alıcı baskısı
# 5) 5m momentum teyidi
# 6) VWAP/EMA benzeri fiyat konumu
# 7) OI sadece güçlü adaylarda
# 8) Pump/late-entry filtresi
# 9) Rate-limit koruması
# ============================================================


# ============================================================
# CONFIG
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

SIGNAL_SCORE = int(os.getenv("SIGNAL_SCORE", "78"))
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "3"))

COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))

DB_PATH = os.getenv("STATE_DB_PATH", "balina_v10.db")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# Erken hareket sınırları
MAX_1M_MOVE = float(os.getenv("MAX_1M_MOVE", "1.20"))
MAX_5M_MOVE = float(os.getenv("MAX_5M_MOVE", "2.50"))
MIN_1M_MOVE = float(os.getenv("MIN_1M_MOVE", "0.05"))

# OI yalnızca bu taban puana ulaşanlarda çağrılır
OI_GATE_SCORE = int(os.getenv("OI_GATE_SCORE", "60"))

# OI bonusu
OI_BONUS = 10

# Rate limit koruması
ERROR_RATE_LIMIT = float(os.getenv("ERROR_RATE_LIMIT", "0.30"))
BACKOFF_MULTIPLIER = int(os.getenv("BACKOFF_MULTIPLIER", "5"))

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
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("balina-v10")


# ============================================================
# HTTP SESSION
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
            **retry_kwargs
        )
    except TypeError:
        retry = Retry(
            method_whitelist=["GET", "POST"],
            **retry_kwargs
        )

    s = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=16,
        pool_maxsize=16,
        max_retries=retry
    )

    s.mount("https://", adapter)
    s.mount("http://", adapter)

    s.headers.update({
        "User-Agent": "BalinaRadari-V10-EarlyIgnition/1.0"
    })

    return s


S = build_session()


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V10 Early Ignition Aktif!"


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V10 Early Ignition",
        "score": SIGNAL_SCORE,
        "scan_interval": SCAN_INTERVAL
    }


# ============================================================
# API
# ============================================================

def api(base, path, params=None):

    r = S.get(
        base + path,
        params=params,
        timeout=TIMEOUT
    )

    r.raise_for_status()

    return r.json()


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
            "%s %s kline hatası: %s",
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

    except Exception as e:

        log.debug(
            "%s OI hatası: %s",
            symbol,
            e
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram(text):

    if not TOKEN or not CHAT:

        log.error(
            "TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik."
        )

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

        return bool(
            r.json().get("ok")
        )

    except Exception as e:

        log.error(
            "Telegram hatası: %s",
            e
        )

        return False


# ============================================================
# HELPERS
# ============================================================

def pct(a, b):

    if a is None or b is None or a <= 0:
        return 0.0

    return ((b - a) / a) * 100


def clamp(x):

    return max(
        0,
        min(
            100,
            int(round(x))
        )
    )


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

    def get_oi(self, symbol):

        with self.lock, sqlite3.connect(self.path) as c:

            row = c.execute(
                "SELECT value, ts FROM oi WHERE symbol=?",
                (symbol,)
            ).fetchone()

        if not row:
            return None

        value, ts = row

        if time.time() - ts > SCAN_INTERVAL * 5:
            return None

        return float(value)

    def put_oi(self, symbol, value):

        if value is None:
            return

        with self.lock, sqlite3.connect(self.path) as c:

            c.execute("""
                INSERT INTO oi(symbol,value,ts)
                VALUES(?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
            """, (
                symbol,
                value,
                time.time()
            ))

    def cooldown(self, symbol):

        with self.lock, sqlite3.connect(self.path) as c:

            row = c.execute(
                "SELECT sent FROM state WHERE symbol=?",
                (symbol,)
            ).fetchone()

        if not row:
            return False

        return (
            time.time() - row[0]
            < COOLDOWN
        )

    def sent(self, symbol, score):

        with self.lock, sqlite3.connect(self.path) as c:

            c.execute("""
                INSERT INTO state(symbol,sent,score)
                VALUES(?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score
            """, (
                symbol,
                time.time(),
                score
            ))


DBS = DB(DB_PATH)


# ============================================================
# CANDIDATES
# ============================================================

def candidates(spot, futures):

    fm = {
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
                "BEARUSDT"
            )
        ):
            continue

        f = fm.get(symbol)

        if not f:
            continue

        try:

            sv = float(
                x.get("quoteVolume", 0)
            )

            fv = float(
                f.get("quoteVolume", 0)
            )

            if sv < MIN_VOLUME:
                continue

            if fv < MIN_VOLUME:
                continue

        except Exception:
            continue

        result.append(symbol)

    return result


# ============================================================
# EARLY SCORE
# ============================================================

def base_score(
    spot_ratio,
    futures_ratio,
    trades_ratio,
    buy_pct,
    move_1m,
    move_5m,
    acceleration
):

    score = 0
    reasons = []

    # --------------------------------------------------------
    # SPOT HACİM
    # --------------------------------------------------------

    if spot_ratio >= 5:
        score += 20
        reasons.append(
            f"🚀 Spot hacmi patladı ({spot_ratio:.1f}x)"
        )

    elif spot_ratio >= 3:
        score += 16
        reasons.append(
            f"🔥 Spot hacmi güçlü ({spot_ratio:.1f}x)"
        )

    elif spot_ratio >= 2:
        score += 10
        reasons.append(
            f"📈 Spot hacmi yükseliyor ({spot_ratio:.1f}x)"
        )

    # --------------------------------------------------------
    # FUTURES
    # --------------------------------------------------------

    if futures_ratio >= 6:
        score += 18
        reasons.append(
            f"⚡ Futures hacmi patladı ({futures_ratio:.1f}x)"
        )

    elif futures_ratio >= 3:
        score += 14
        reasons.append(
            f"⚡ Futures hacmi güçlü ({futures_ratio:.1f}x)"
        )

    elif futures_ratio >= 2:
        score += 8
        reasons.append(
            f"📊 Futures aktivitesi artıyor ({futures_ratio:.1f}x)"
        )

    # --------------------------------------------------------
    # TRADE COUNT
    # --------------------------------------------------------

    if trades_ratio >= 3:
        score += 18
        reasons.append(
            f"🤖 İşlem akışı patladı ({trades_ratio:.1f}x)"
        )

    elif trades_ratio >= 2:
        score += 14
        reasons.append(
            f"📈 İşlem sayısı güçlü ({trades_ratio:.1f}x)"
        )

    elif trades_ratio >= 1.5:
        score += 8
        reasons.append(
            f"📊 İşlem aktivitesi artıyor ({trades_ratio:.1f}x)"
        )

    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

    if buy_pct >= 75:

        score += 18

        reasons.append(
            f"🐋 Çok güçlü alıcı baskısı (%{buy_pct:.1f})"
        )

    elif buy_pct >= 65:

        score += 14

        reasons.append(
            f"🟢 Güçlü alıcı baskısı (%{buy_pct:.1f})"
        )

    elif buy_pct >= 58:

        score += 8

        reasons.append(
            f"📈 Pozitif alıcı baskısı (%{buy_pct:.1f})"
        )

    # --------------------------------------------------------
    # ERKEN FİYAT HAREKETİ
    # --------------------------------------------------------

    if (
        MIN_1M_MOVE <= move_1m <= 0.60
    ):

        score += 12

        reasons.append(
            f"🎯 Fiyat hâlâ erken (+%{move_1m:.2f})"
        )

    elif move_1m <= 1.0:

        score += 7

        reasons.append(
            f"📈 Kontrollü başlangıç (+%{move_1m:.2f})"
        )

    # --------------------------------------------------------
    # 5M KONTROLLÜ MOMENTUM
    # --------------------------------------------------------

    if 0.20 <= move_5m <= 1.80:

        score += 8

        reasons.append(
            f"🎯 5m momentum sağlıklı (+%{move_5m:.2f})"
        )

    # --------------------------------------------------------
    # ACCELERATION
    # --------------------------------------------------------

    if acceleration >= 1.5:

        score += 10

        reasons.append(
            f"🔥 Hacim ivmesi güçlü ({acceleration:.2f}x)"
        )

    elif acceleration >= 1.15:

        score += 5

        reasons.append(
            f"📈 Hacim ivmesi artıyor ({acceleration:.2f}x)"
        )

    return clamp(score), reasons


# ============================================================
# ANALYSIS
# ============================================================

def analyze(symbol):

    try:

        # ----------------------------------------------------
        # VERİ
        # ----------------------------------------------------

        sp1 = klines(
            SPOT,
            symbol,
            "1m",
            35
        )

        fu1 = klines(
            FUT,
            symbol,
            "1m",
            35
        )

        sp5 = klines(
            SPOT,
            symbol,
            "5m",
            12
        )

        if (
            len(sp1) < 25
            or len(fu1) < 25
            or len(sp5) < 8
        ):

            return {
                "status": "insufficient"
            }

        # ----------------------------------------------------
        # CANLI MUM
        # ----------------------------------------------------

        live = sp1[-1]

        price = float(live[4])
        open_price = float(live[1])

        move_1m = pct(
            open_price,
            price
        )

        # ----------------------------------------------------
        # GEÇ KALMA FİLTRESİ
        # ----------------------------------------------------

        if move_1m > MAX_1M_MOVE:

            return {
                "status": "late",
                "score": 0
            }

        if move_1m < -1:

            return {
                "status": "weak",
                "score": 0
            }

        close5 = [
            float(x[4])
            for x in sp5
        ]

        move_5m = pct(
            close5[-2],
            price
        )

        if move_5m > MAX_5M_MOVE:

            return {
                "status": "late",
                "score": 0
            }

        # ----------------------------------------------------
        # KAPALI 1M MUMLAR
        # ----------------------------------------------------

        closed = sp1[:-1]
        fclosed = fu1[:-1]

        volumes = [
            float(x[7])
            for x in closed
        ]

        fvolumes = [
            float(x[7])
            for x in fclosed
        ]

        trades = [
            int(x[8])
            for x in closed
        ]

        # ----------------------------------------------------
        # ORTALAMALAR
        # ----------------------------------------------------

        avg_volume = (
            sum(volumes[-15:])
            / 15
        )

        avg_fvolume = (
            sum(fvolumes[-15:])
            / 15
        )

        avg_trades = (
            sum(trades[-15:])
            / 15
        )

        if (
            avg_volume <= 0
            or avg_fvolume <= 0
            or avg_trades <= 0
        ):

            return {
                "status": "insufficient"
            }

        # ----------------------------------------------------
        # SON 3 DAKİKA
        # ----------------------------------------------------

        recent_volume = (
            sum(
                float(x[7])
                for x in sp1[-4:-1]
            ) / 3
        )

        recent_fvolume = (
            sum(
                float(x[7])
                for x in fu1[-4:-1]
            ) / 3
        )

        recent_trades = (
            sum(
                int(x[8])
                for x in sp1[-4:-1]
            ) / 3
        )

        spot_ratio = (
            recent_volume
            / avg_volume
        )

        futures_ratio = (
            recent_fvolume
            / avg_fvolume
        )

        trades_ratio = (
            recent_trades
            / avg_trades
        )

        # ----------------------------------------------------
        # CANLI ALIM BASKISI
        # ----------------------------------------------------

        live_volume = float(
            live[7]
        )

        live_buy = float(
            live[10]
        )

        buy_pct = (
            live_buy
            / live_volume
            * 100
            if live_volume > 0
            else 50
        )

        # ----------------------------------------------------
        # HACİM İVMESİ
        # ----------------------------------------------------

        previous_volume = (
            sum(volumes[-6:-3])
            / 3
        )

        acceleration = (
            recent_volume
            / previous_volume
            if previous_volume > 0
            else 1
        )

        # ----------------------------------------------------
        # TEMEL PUAN
        # ----------------------------------------------------

        score, reasons = base_score(
            spot_ratio,
            futures_ratio,
            trades_ratio,
            buy_pct,
            move_1m,
            move_5m,
            acceleration
        )

        # ----------------------------------------------------
        # ANA KAPI
        # ----------------------------------------------------

        # Zayıf hacim + zayıf işlem akışı = OI sorgulama.
        if (
            spot_ratio < 1.5
            or trades_ratio < 1.25
        ):

            return {
                "status": "below_score",
                "score": score
            }

        # ----------------------------------------------------
        # PUMP KORUMASI
        # ----------------------------------------------------

        if move_5m > 1.8 and move_1m > 0.8:

            return {
                "status": "late",
                "score": score
            }

        # ----------------------------------------------------
        # OI SADECE GÜÇLÜ ADAYLARDA
        # ----------------------------------------------------

        oi_change = 0.0

        if score >= OI_GATE_SCORE:

            now_oi = open_interest(symbol)
            old_oi = DBS.get_oi(symbol)

            if (
                now_oi is not None
                and old_oi is not None
            ):

                oi_change = pct(
                    old_oi,
                    now_oi
                )

                # OI artışı + erken fiyat hareketi
                if oi_change >= 0.8:

                    score += OI_BONUS

                    reasons.append(
                        f"📈 OI yeni pozisyonlarla artıyor (+%{oi_change:.2f})"
                    )

                elif oi_change < -2:

                    score -= 5

                    reasons.append(
                        f"⚠️ OI zayıflıyor (%{oi_change:.2f})"
                    )

            DBS.put_oi(
                symbol,
                now_oi
                if 'now_oi' in locals()
                else None
            )

        score = clamp(score)

        # ----------------------------------------------------
        # SON FİLTRE
        # ----------------------------------------------------

        if score < SIGNAL_SCORE:

            return {
                "status": "below_score",
                "score": score
            }

        # ----------------------------------------------------
        # EK GÜVENLİK
        # ----------------------------------------------------

        if buy_pct < 55:

            return {
                "status": "weak",
                "score": score
            }

        if acceleration < 0.90:

            return {
                "status": "weak",
                "score": score
            }

        # ----------------------------------------------------
        # SİNYAL
        # ----------------------------------------------------

        return {

            "status": "signal",

            "symbol": symbol,

            "score": score,

            "price": price,

            "spot_ratio": spot_ratio,

            "futures_ratio": futures_ratio,

            "trades_ratio": trades_ratio,

            "buy_pct": buy_pct,

            "move_1m": move_1m,

            "move_5m": move_5m,

            "acceleration": acceleration,

            "oi": oi_change,

            "reasons": reasons
        }

    except Exception as e:

        log.debug(
            "%s analiz hatası: %s",
            symbol,
            e
        )

        return {
            "status": "error"
        }


# ============================================================
# MESSAGE
# ============================================================

def message(r):

    reasons = "\n".join(
        "• " + x
        for x in r["reasons"]
    )

    return (
        "🐋 BALİNA RADARI V10\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "🎯 ERKEN YÜKSELİŞ SİNYALİ\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 Fiyat: {r['price']:.8g}\n"
        f"🏆 SCORE: {r['score']}/100\n\n"

        "📊 PİYASA AKIŞI\n"
        f"• Spot hacim: {r['spot_ratio']:.2f}x\n"
        f"• Futures hacim: {r['futures_ratio']:.2f}x\n"
        f"• İşlem sayısı: {r['trades_ratio']:.2f}x\n"
        f"• Alıcı baskısı: %{r['buy_pct']:.1f}\n\n"

        "🚀 ERKEN HAREKET\n"
        f"• 1m: +%{r['move_1m']:.2f}\n"
        f"• 5m: +%{r['move_5m']:.2f}\n"
        f"• Hacim ivmesi: {r['acceleration']:.2f}x\n"
        f"• OI: %{r['oi']:.2f}\n\n"

        "🔎 TEYİTLER\n"
        f"{reasons}\n\n"

        "⚠️ Amaç yükselmiş coinleri kovalamak "
        "değil, yükselişin erken aşamasını yakalamaktır."
    )


# ============================================================
# SCAN
# ============================================================

def scan():

    spot = tickers(SPOT)
    futures = tickers(FUT)

    if not spot or not futures:

        log.warning(
            "Ticker alınamadı. Rate-limit koruması devrede."
        )

        return True

    cs = candidates(
        spot,
        futures
    )

    signals = []
    stats = {}

    log.info(
        "🔎 %d aday erken hareket filtresine giriyor...",
        len(cs)
    )

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as executor:

        jobs = [
            executor.submit(
                analyze,
                symbol
            )
            for symbol in cs
        ]

        for future in as_completed(jobs):

            result = future.result()

            status = result.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0) + 1
            )

            if status == "signal":

                signals.append(result)

    # --------------------------------------------------------
    # EN GÜÇLÜLER
    # --------------------------------------------------------

    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    sent = 0

    for r in signals[:MAX_SIGNALS]:

        symbol = r["symbol"]

        if DBS.cooldown(symbol):

            continue

        if telegram(
            message(r)
        ):

            DBS.sent(
                symbol,
                r["score"]
            )

            sent += 1

        time.sleep(0.5)

    log.info(
        "🐋 V10 SONUÇ | "
        "Aday:%d | "
        "Sinyal:%d | "
        "Alt:%d | "
        "Geç:%d | "
        "Zayıf:%d | "
        "Hata:%d",

        len(cs),
        sent,
        stats.get("below_score", 0),
        stats.get("late", 0),
        stats.get("weak", 0),
        stats.get("error", 0)
    )

    # --------------------------------------------------------
    # RATE LIMIT DEVRE KESİCİ
    # --------------------------------------------------------

    total = max(
        1,
        len(cs)
    )

    error_rate = (
        stats.get("error", 0)
        / total
    )

    return (
        error_rate
        >= ERROR_RATE_LIMIT
    )


# ============================================================
# LOOP
# ============================================================

def loop():

    log.info(
        "🐋 BALİNA RADARI V10 "
        "EARLY IGNITION BAŞLADI"
    )

    if not TOKEN or not CHAT:

        log.warning(
            "Telegram bilgileri eksik."
        )

    else:

        telegram(
            "🐋 BALİNA RADARI V10 AKTİF\n\n"
            "🎯 Erken yükseliş motoru\n"
            "🚫 Geç kalmış hareket filtresi\n"
            "📊 Spot + Futures + Trade Count\n"
            "🐋 Alıcı baskısı\n"
            "🔥 Hacim ivmesi\n"
            "📈 OI yalnızca güçlü adaylarda\n"
            "🛡️ Rate-limit koruması aktif"
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

        # ----------------------------------------------------
        # DEVRE KESİCİ
        # ----------------------------------------------------

        if backoff:

            wait = (
                SCAN_INTERVAL
                * BACKOFF_MULTIPLIER
            )

            log.warning(
                "🛑 Devre kesici: "
                "%d saniye bekleniyor.",
                wait
            )

            time.sleep(wait)

        else:

            remaining = max(
                1,
                SCAN_INTERVAL
                - elapsed
            )

            time.sleep(
                remaining
            )


# ============================================================
# START
# ============================================================

Thread(
    target=loop,
    daemon=True,
    name="balina-v10"
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
