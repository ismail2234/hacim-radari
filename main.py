
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
# 🐋 BALİNA RADARI V11 — EARLY FLOW
# Amaç:
# - Yükselmiş coinleri kovalamamak
# - Yükselişin ilk dakikalarını yakalamak
# - Spot + Futures + işlem sayısı + alıcı baskısı
# - Hacim ivmesi
# - Canlı 1m mum
# - 5m momentum
# - OI sadece güçlü adaylarda
# - Eksik veride sinyal üretmemek
# - Rate-limit riskini azaltmak
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "78"))
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))

TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v11.db")

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
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("balina-v11")


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
            **retry_kwargs
        )
    except TypeError:
        retry = Retry(
            method_whitelist=["GET", "POST"],
            **retry_kwargs
        )

    s = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20,
        max_retries=retry,
    )

    s.mount("https://", adapter)

    s.headers.update({
        "User-Agent": "BalinaRadari-V11/1.0"
    })

    return s


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


# ============================================================
# TELEGRAM
# ============================================================

def telegram(text):

    if not TOKEN or not CHAT:
        log.warning("Telegram bilgileri eksik.")
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

        return bool(response.json().get("ok"))

    except Exception as e:

        log.error("Telegram hatası: %s", e)

        return False


# ============================================================
# BINANCE
# ============================================================

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
                "limit": limit,
            },
        )

    except Exception as e:

        log.debug(
            "%s %s kline hatası: %s",
            symbol,
            interval,
            e,
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

    if a is None or b is None or a <= 0:
        return 0.0

    return ((b - a) / a) * 100


def clamp(value):

    return max(
        0,
        min(
            100,
            int(round(value))
        )
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
                """
                SELECT value, ts
                FROM oi
                WHERE symbol=?
                """,
                (symbol,),
            ).fetchone()

        if not row:
            return None

        value, timestamp = row

        if time.time() - timestamp > SCAN_INTERVAL * 5:
            return None

        return float(value)


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


    def cooldown(self, symbol):

        with self.lock, sqlite3.connect(self.path) as db:

            row = db.execute(
                """
                SELECT sent
                FROM state
                WHERE symbol=?
                """,
                (symbol,),
            ).fetchone()

        if not row:
            return False

        return (
            time.time() - row[0]
            < COOLDOWN
        )


    def sent(self, symbol, score):

        with self.lock, sqlite3.connect(self.path) as db:

            db.execute(
                """
                INSERT INTO state(symbol,sent,score)
                VALUES(?,?,?)

                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score
                """,
                (
                    symbol,
                    time.time(),
                    score,
                ),
            )


DBS = DB(DB_PATH)


# ============================================================
# ADAY FİLTRESİ
# ============================================================

def candidates(spot, futures):

    futures_map = {
        x.get("symbol"): x
        for x in futures
    }

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

            future_volume = float(
                future.get("quoteVolume", 0)
            )

            daily_change = float(
                item.get("priceChangePercent", 0)
            )

            if spot_volume < MIN_VOLUME:
                continue

            if future_volume < MIN_VOLUME:
                continue

            # Günlük olarak zaten uçmuş coinleri ele.
            if daily_change > 18:
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

        # ----------------------------------------------------
        # VERİLER
        # ----------------------------------------------------

        spot_1m = klines(
            SPOT,
            symbol,
            "1m",
            32,
        )

        futures_1m = klines(
            FUT,
            symbol,
            "1m",
            32,
        )

        spot_5m = klines(
            SPOT,
            symbol,
            "5m",
            12,
        )

        if (
            len(spot_1m) < 28
            or len(futures_1m) < 28
            or len(spot_5m) < 8
        ):
            return {
                "status": "insufficient"
            }


        # ----------------------------------------------------
        # CANLI MUM
        # ----------------------------------------------------

        live = spot_1m[-1]

        price = float(live[4])
        live_open = float(live[1])

        live_change = pct(
            live_open,
            price,
        )


        # ----------------------------------------------------
        # GEÇ KALMA FİLTRESİ
        # ----------------------------------------------------

        if live_change > 1.25:

            return {
                "status": "late"
            }


        if live_change < -1.25:

            return {
                "status": "weak"
            }


        # ----------------------------------------------------
        # 5M MOMENTUM
        # ----------------------------------------------------

        closes_5m = [
            float(x[4])
            for x in spot_5m
        ]

        momentum_5m = pct(
            closes_5m[-2],
            price,
        )

        momentum_15m = pct(
            closes_5m[-4],
            price,
        )


        # Aşırı kaçmış hareket.
        if momentum_5m > 3.0:
            return {
                "status": "late"
            }

        if momentum_15m > 5.0:
            return {
                "status": "late"
            }


        # ----------------------------------------------------
        # 1M VERİ
        # ----------------------------------------------------

        spot_closed = spot_1m[:-1]
        futures_closed = futures_1m[:-1]

        spot_volumes = [
            float(x[7])
            for x in spot_1m
        ]

        futures_volumes = [
            float(x[7])
            for x in futures_1m
        ]

        trade_counts = [
            float(x[8])
            for x in spot_1m
        ]


        # ----------------------------------------------------
        # NORMAL TABAN
        # ----------------------------------------------------

        average_spot = (
            sum(
                float(x[7])
                for x in spot_closed[-15:]
            )
            / 15
        )

        average_futures = (
            sum(
                float(x[7])
                for x in futures_closed[-15:]
            )
            / 15
        )

        average_trades = (
            sum(
                float(x[8])
                for x in spot_closed[-15:]
            )
            / 15
        )


        if (
            average_spot <= 0
            or average_futures <= 0
            or average_trades <= 0
        ):
            return {
                "status": "insufficient"
            }


        # ----------------------------------------------------
        # SON 3 DAKİKA
        # ----------------------------------------------------

        recent_spot = (
            sum(spot_volumes[-3:])
            / 3
        )

        recent_futures = (
            sum(futures_volumes[-3:])
            / 3
        )

        recent_trades = (
            sum(trade_counts[-3:])
            / 3
        )


        spot_ratio = (
            recent_spot
            / average_spot
        )

        futures_ratio = (
            recent_futures
            / average_futures
        )

        trade_ratio = (
            recent_trades
            / average_trades
        )


        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------

        live_volume = float(live[7])
        live_buy_volume = float(live[10])

        if live_volume > 0:

            buyer_pressure = (
                live_buy_volume
                / live_volume
                * 100
            )

        else:

            buyer_pressure = 50


        # ----------------------------------------------------
        # HACİM İVMESİ
        # ----------------------------------------------------

        previous_spot = (
            sum(spot_volumes[-6:-3])
            / 3
        )

        if previous_spot > 0:

            volume_acceleration = (
                recent_spot
                / previous_spot
            )

        else:

            volume_acceleration = 1


        # ----------------------------------------------------
        # PUAN
        # ----------------------------------------------------

        score = 0
        reasons = []


        # SPOT
        if spot_ratio >= 4:

            score += 18

            reasons.append(
                f"🔥 Spot hacmi çok güçlü ({spot_ratio:.2f}x)"
            )

        elif spot_ratio >= 3:

            score += 15

            reasons.append(
                f"🔥 Spot hacmi güçlü ({spot_ratio:.2f}x)"
            )

        elif spot_ratio >= 2:

            score += 10

            reasons.append(
                f"📈 Spot hacmi artıyor ({spot_ratio:.2f}x)"
            )


        # FUTURES
        if futures_ratio >= 4:

            score += 17

            reasons.append(
                f"⚡ Futures hacmi çok güçlü ({futures_ratio:.2f}x)"
            )

        elif futures_ratio >= 2.5:

            score += 14

            reasons.append(
                f"⚡ Futures aktivitesi güçlü ({futures_ratio:.2f}x)"
            )

        elif futures_ratio >= 2:

            score += 10

            reasons.append(
                f"📊 Futures aktivitesi artıyor ({futures_ratio:.2f}x)"
            )


        # TRADE COUNT
        if trade_ratio >= 3:

            score += 17

            reasons.append(
                f"📈 İşlem sayısı patlıyor ({trade_ratio:.2f}x)"
            )

        elif trade_ratio >= 2:

            score += 14

            reasons.append(
                f"📈 İşlem sayısı güçlü ({trade_ratio:.2f}x)"
            )

        elif trade_ratio >= 1.5:

            score += 8

            reasons.append(
                f"📊 İşlem sayısı artıyor ({trade_ratio:.2f}x)"
            )


        # ALICI
        if buyer_pressure >= 80:

            score += 18

            reasons.append(
                f"🐋 Çok güçlü alıcı baskısı (%{buyer_pressure:.1f})"
            )

        elif buyer_pressure >= 70:

            score += 15

            reasons.append(
                f"🟢 Güçlü alıcı baskısı (%{buyer_pressure:.1f})"
            )

        elif buyer_pressure >= 60:

            score += 9

            reasons.append(
                f"🟢 Pozitif alıcı baskısı (%{buyer_pressure:.1f})"
            )


        # ----------------------------------------------------
        # ERKEN FİYAT
        # ----------------------------------------------------

        if (
            0.05
            <= live_change
            <= 0.55
        ):

            score += 14

            reasons.append(
                f"🎯 Fiyat erken aşamada (+%{live_change:.2f})"
            )

        elif (
            -0.05
            <= live_change
            < 0.05
            and buyer_pressure >= 70
        ):

            score += 11

            reasons.append(
                f"🎯 Fiyat yatay, alım baskısı birikiyor (%{buyer_pressure:.1f})"
            )

        elif (
            0.55
            < live_change
            <= 1.25
        ):

            score += 5

            reasons.append(
                f"📈 Hareket başladı (+%{live_change:.2f})"
            )


        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        if (
            0.10
            <= momentum_5m
            <= 1.50
        ):

            score += 9

            reasons.append(
                f"🎯 5m momentum sağlıklı (+%{momentum_5m:.2f})"
            )

        elif (
            -0.15
            <= momentum_5m
            < 0.10
            and buyer_pressure >= 75
        ):

            score += 5

            reasons.append(
                f"📊 5m sıkışma/birikim (+%{momentum_5m:.2f})"
            )


        # ----------------------------------------------------
        # HACİM İVMESİ
        # ----------------------------------------------------

        if volume_acceleration >= 5:

            score += 8

            reasons.append(
                f"🚀 Hacim ivmesi aşırı güçlü ({volume_acceleration:.2f}x)"
            )

        elif volume_acceleration >= 2.5:

            score += 6

            reasons.append(
                f"🔥 Hacim ivmesi güçlü ({volume_acceleration:.2f}x)"
            )

        elif volume_acceleration >= 1.5:

            score += 3

            reasons.append(
                f"📈 Hacim ivmesi artıyor ({volume_acceleration:.2f}x)"
            )


        # ----------------------------------------------------
        # BAĞIMSIZ TEYİT SAYISI
        # ----------------------------------------------------

        confirmations = sum([
            spot_ratio >= 2,
            futures_ratio >= 2,
            trade_ratio >= 1.5,
            buyer_pressure >= 60,
            live_change <= 1.25,
            -0.15 <= momentum_5m <= 1.5,
            volume_acceleration >= 1.5,
        ])


        if confirmations < 5:

            return {
                "status": "weak",
                "score": clamp(score),
            }


        # ----------------------------------------------------
        # OI
        # Sadece güçlü adaylarda çağrılır.
        # Böylece gereksiz Binance isteği azaltılır.
        # ----------------------------------------------------

        oi_change = None

        if score >= SIGNAL_THRESHOLD - 10:

            now_oi = open_interest(symbol)
            old_oi = DBS.get_oi(symbol)

            if (
                old_oi is not None
                and now_oi is not None
            ):

                oi_change = pct(
                    old_oi,
                    now_oi,
                )

                if oi_change >= 1:

                    score += 7

                    reasons.append(
                        f"📈 OI destekli (+%{oi_change:.2f})"
                    )

                elif (
                    oi_change <= -1
                    and buyer_pressure < 65
                ):

                    score -= 5

                    reasons.append(
                        f"⚠️ OI zayıflıyor (%{oi_change:.2f})"
                    )

            DBS.put_oi(
                symbol,
                now_oi,
            )


        score = clamp(score)


        # ----------------------------------------------------
        # SON EŞİK
        # ----------------------------------------------------

        if score < SIGNAL_THRESHOLD:

            return {
                "status": "below",
                "score": score,
            }


        # ----------------------------------------------------
        # KALİTE
        # ----------------------------------------------------

        if score >= 90:

            signal_type = "🔥 ULTRA ERKEN"

        elif score >= 85:

            signal_type = "🚀 GÜÇLÜ ERKEN"

        else:

            signal_type = "🎯 ERKEN YÜKSELİŞ"


        return {
            "status": "signal",
            "symbol": symbol,
            "type": signal_type,
            "score": score,
            "price": price,
            "spot_ratio": spot_ratio,
            "futures_ratio": futures_ratio,
            "trade_ratio": trade_ratio,
            "buyer_pressure": buyer_pressure,
            "live_change": live_change,
            "momentum_5m": momentum_5m,
            "momentum_15m": momentum_15m,
            "volume_acceleration": volume_acceleration,
            "oi": oi_change,
            "reasons": reasons,
        }


    except Exception as e:

        log.debug(
            "%s analiz hatası: %s",
            symbol,
            e,
        )

        return {
            "status": "error"
        }


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def message(r):

    if r["oi"] is None:
        oi_text = "veri bekleniyor"
    else:
        oi_text = f"%{r['oi']:.2f}"


    return (
        "🐋 BALİNA RADARI V11\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🎯 {r['type']}\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 Fiyat: {r['price']:.8g}\n"
        f"🏆 SCORE: {r['score']}/100\n\n"

        "📊 PİYASA AKIŞI\n"
        f"• Spot hacim: {r['spot_ratio']:.2f}x\n"
        f"• Futures hacim: {r['futures_ratio']:.2f}x\n"
        f"• İşlem sayısı: {r['trade_ratio']:.2f}x\n"
        f"• Alıcı baskısı: %{r['buyer_pressure']:.1f}\n\n"

        "🚀 ERKEN HAREKET\n"
        f"• 1m: +%{r['live_change']:.2f}\n"
        f"• 5m: +%{r['momentum_5m']:.2f}\n"
        f"• 15m: +%{r['momentum_15m']:.2f}\n"
        f"• Hacim ivmesi: {r['volume_acceleration']:.2f}x\n"
        f"• OI: {oi_text}\n\n"

        "🔎 TEYİTLER\n"
        + "\n".join(
            "• " + x
            for x in r["reasons"]
        )

        + "\n\n"
        "⚠️ Amaç yükselmiş coinleri kovalamak değil, "
        "yükselişin erken aşamasını yakalamaktır."
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
            "Ticker verisi alınamadı. Koruma devreye giriyor."
        )

        return True


    symbols = candidates(
        spot,
        futures,
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

            if status == "signal":

                signals.append(result)


    signals.sort(
        key=lambda x: x["score"],
        reverse=True,
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

    error_rate = errors / total


    log.info(
        "🐋 V11 | Aday:%d | "
        "Sinyal:%d | Alt:%d | "
        "Geç:%d | Hata:%d | Süre:%.1fs",

        len(symbols),
        sent,
        stats.get("below", 0),
        stats.get("late", 0),
        errors,
        elapsed,
    )


    # Rate-limit / sistem yetişememe koruması.
    if (
        error_rate > 0.30
        or elapsed > SCAN_INTERVAL * 1.25
    ):

        return True


    return False


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():

    return (
        "🐋 Balina Radarı V11 "
        "Early Flow Aktif!"
    )


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V11",
        "threshold": SIGNAL_THRESHOLD,
        "interval": SCAN_INTERVAL,
    }


# ============================================================
# LOOP
# ============================================================

def loop():

    log.info(
        "🐋 BALİNA RADARI V11 başlatılıyor..."
    )


    if TOKEN and CHAT:

        telegram(
            "🐋 BALİNA RADARI V11 AKTİF\n\n"
            "🎯 Erken yükseliş odaklı\n"
            "📊 Spot + Futures + Trade Count\n"
            "🚀 Hacim ivmesi\n"
            "🕯️ Canlı 1m + 5m momentum\n"
            "🚫 Geç kalmış pump filtresi\n"
            "🛡️ Rate-limit koruması"
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
                "🛑 Koruma beklemesi: %d saniye",
                wait,
            )

            time.sleep(wait)

        else:

            time.sleep(
                max(
                    1,
                    SCAN_INTERVAL - elapsed
                )
            )


# ============================================================
# START
# ============================================================

Thread(
    target=loop,
    daemon=True,
    name="balina-v11"
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
        use_reloader=False,
    )
