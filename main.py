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
# BALINA RADARI V16 - BREAKOUT PREDICTOR
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

PREPARE_THRESHOLD = int(os.getenv("PREPARE_THRESHOLD", "70"))
STRONG_THRESHOLD = int(os.getenv("STRONG_THRESHOLD", "82"))
VERY_STRONG_THRESHOLD = int(os.getenv("VERY_STRONG_THRESHOLD", "90"))

MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v16.db")

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
log = logging.getLogger("balina-v16")


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
        retry = Retry(allowed_methods=["GET", "POST"], **retry_kwargs)
    except TypeError:
        retry = Retry(method_whitelist=["GET", "POST"], **retry_kwargs)

    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=20,
        pool_maxsize=20,
        max_retries=retry
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "BalinaRadari-V16/1.0"})
    return session


S = build_session()


def api(base, path, params=None):
    response = S.get(
        base + path,
        params=params,
        timeout=TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def telegram(text):
    if not TOKEN or not CHAT:
        log.warning("Telegram ayarlari eksik.")
        return False

    try:
        response = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT, "text": text},
            timeout=TIMEOUT
        )
        response.raise_for_status()
        return bool(response.json().get("ok"))
    except Exception as e:
        log.error("Telegram hatasi: %s", e)
        return False


def tickers(base):
    try:
        path = "/api/v3/ticker/24hr" if base == SPOT else "/fapi/v1/ticker/24hr"
        return api(base, path)
    except Exception as e:
        log.error("Ticker hatasi: %s", e)
        return []


def klines(base, symbol, interval, limit):
    try:
        path = "/api/v3/klines" if base == SPOT else "/fapi/v1/klines"
        return api(
            base,
            path,
            {"symbol": symbol, "interval": interval, "limit": limit}
        )
    except Exception as e:
        log.debug("%s %s kline hatasi: %s", symbol, interval, e)
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


def pct(a, b):
    if a is None or b is None or a <= 0:
        return 0.0
    return ((b - a) / a) * 100.0


def average(values):
    return sum(values) / len(values) if values else 0.0


def clamp(value):
    return max(0, min(100, int(round(value))))


class DB:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()

        with self.lock, sqlite3.connect(path) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL,
                    score REAL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS oi(
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    ts REAL
                )
            """)

    def get_oi(self, symbol):
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT value, ts FROM oi WHERE symbol=?",
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
            db.execute("""
                INSERT INTO oi(symbol,value,ts)
                VALUES(?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    value=excluded.value,
                    ts=excluded.ts
            """, (symbol, value, time.time()))

    def cooldown(self, symbol):
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT sent FROM state WHERE symbol=?",
                (symbol,)
            ).fetchone()

        return bool(
            row and time.time() - row[0] < COOLDOWN
        )

    def sent(self, symbol, score):
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("""
                INSERT INTO state(symbol,sent,score)
                VALUES(?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score
            """, (symbol, time.time(), score))


DBS = DB(DB_PATH)


def candidates(spot, futures):
    futures_map = {x.get("symbol"): x for x in futures}
    result = []

    for item in spot:
        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue
        if symbol in EXCLUDED:
            continue
        if any(
            symbol.endswith(x)
            for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
        ):
            continue

        future = futures_map.get(symbol)
        if not future:
            continue

        try:
            spot_volume = float(item.get("quoteVolume", 0))
            futures_volume = float(future.get("quoteVolume", 0))
            daily_change = float(item.get("priceChangePercent", 0))

            if spot_volume < MIN_VOLUME:
                continue
            if futures_volume < MIN_VOLUME:
                continue
            if daily_change > 16.0:
                continue

            result.append(symbol)

        except (TypeError, ValueError):
            continue

    return result


def analyze(symbol):
    try:
        spot_1m = klines(SPOT, symbol, "1m", 50)
        futures_1m = klines(FUT, symbol, "1m", 40)
        spot_5m = klines(SPOT, symbol, "5m", 20)

        if (
            len(spot_1m) < 35
            or len(futures_1m) < 30
            or len(spot_5m) < 10
        ):
            return {"status": "insufficient"}

        live = spot_1m[-1]
        price = float(live[4])
        live_open = float(live[1])
        live_low = float(live[3])
        live_change = pct(live_open, price)

        closes_5m = [float(x[4]) for x in spot_5m]
        m5 = pct(closes_5m[-2], price)
        m15 = pct(closes_5m[-4], price)
        m30 = pct(closes_5m[-7], price)

        if (
            live_change > 1.20
            or m5 > 2.50
            or m15 > 4.50
            or m30 > 7.0
        ):
            return {"status": "late"}

        if live_change < -2.0 or m5 < -3.5:
            return {"status": "weak"}

        closed = spot_1m[:-1]

        closes = [float(x[4]) for x in closed]
        highs = [float(x[2]) for x in closed]
        lows = [float(x[3]) for x in closed]

        low_30 = min(lows[-30:])
        high_30 = max(highs[-30:])

        location = (
            (price - low_30) / (high_30 - low_30) * 100.0
            if high_30 > low_30 else 50.0
        )

        very_low = location <= 22.0
        low_zone = location <= 35.0
        near_low = location <= 45.0

        last_lows = lows[-6:]
        lowest_recent = min(last_lows)

        base_holding = (
            price >= lowest_recent
            and lows[-1] >= lowest_recent
        )

        low_range = max(last_lows[-3:]) - min(last_lows[-3:])
        base_width_pct = low_range / price * 100.0 if price > 0 else 99.0
        base_compressed = base_width_pct <= 0.70
        base_formed = base_holding and base_compressed

        ma7 = average(closes[-7:])
        ma20 = average(closes[-20:])
        ma30 = average(closes[-30:])

        ma_diff = abs(ma7 - ma30) / price * 100.0
        ma_squeeze = ma_diff <= 0.75

        ma7_prev = average(closes[-10:-3])
        ma_turning_up = ma7 >= ma7_prev

        ranges = [
            (float(x[2]) - float(x[3])) / float(x[4]) * 100.0
            for x in closed[-12:]
            if float(x[4]) > 0
        ]

        recent_range = average(ranges[-3:])
        older_range = average(ranges[-9:-3])

        volatility_compression = (
            older_range > 0
            and recent_range < older_range * 0.75
        )

        resistance = max(highs[-8:])
        breakout_distance = (
            (resistance - price) / price * 100.0
            if price > 0 else 99.0
        )

        near_breakout = 0.0 <= breakout_distance <= 0.80
        very_near_breakout = 0.0 <= breakout_distance <= 0.40
        resistance_test = price >= resistance * 0.997

        a = closed[-1]
        b = closed[-2]
        c = closed[-3]

        a_open = float(a[1])
        a_high = float(a[2])
        a_low = float(a[3])
        a_close = float(a[4])

        b_high = float(b[2])
        b_low = float(b[3])
        c_low = float(c[3])

        higher_low = a_low > b_low and a_low >= c_low
        break_high = price > a_high

        body = abs(a_close - a_open)
        lower_wick = min(a_open, a_close) - a_low
        candle_range = a_high - a_low

        wick_rejection = (
            candle_range > 0
            and lower_wick / candle_range >= 0.35
            and lower_wick >= body * 0.8
        )

        reversal = higher_low or break_high or wick_rejection

        spot_volumes = [float(x[7]) for x in spot_1m]
        futures_volumes = [float(x[7]) for x in futures_1m]
        trades = [float(x[8]) for x in spot_1m]

        avg_spot = average(spot_volumes[-19:-1])
        avg_futures = average(futures_volumes[-19:-1])
        avg_trades = average(trades[-19:-1])

        if min(avg_spot, avg_futures, avg_trades) <= 0:
            return {"status": "insufficient"}

        spot_ratio = average(spot_volumes[-3:]) / avg_spot
        futures_ratio = average(futures_volumes[-3:]) / avg_futures
        trade_ratio = average(trades[-3:]) / avg_trades

        previous_volume = average(spot_volumes[-6:-3])
        current_volume = average(spot_volumes[-3:])

        volume_acceleration = (
            current_volume / previous_volume
            if previous_volume > 0 else 1.0
        )

        buy3 = sum(float(x[10]) for x in spot_1m[-3:])
        vol3 = sum(float(x[7]) for x in spot_1m[-3:])
        buy5 = sum(float(x[10]) for x in spot_1m[-5:])
        vol5 = sum(float(x[7]) for x in spot_1m[-5:])

        bp3 = buy3 / vol3 * 100.0 if vol3 > 0 else 50.0
        bp5 = buy5 / vol5 * 100.0 if vol5 > 0 else 50.0
        buyer_pressure = bp3 * 0.65 + bp5 * 0.35

        silent_accumulation = (
            -0.35 <= live_change <= 0.45
            and (spot_ratio >= 2.0 or futures_ratio >= 2.0)
            and buyer_pressure >= 62.0
        )

        spot_leading = (
            spot_ratio >= 2.0
            and spot_ratio >= futures_ratio * 1.10
        )

        oi_change = None

        preliminary_strength = (
            low_zone
            and buyer_pressure >= 62.0
            and (spot_ratio >= 2.0 or silent_accumulation)
        )

        if preliminary_strength:
            now_oi = open_interest(symbol)
            old_oi = DBS.get_oi(symbol)

            if now_oi is not None and old_oi is not None:
                oi_change = pct(old_oi, now_oi)

            DBS.put_oi(symbol, now_oi)

        falling = (
            m5 < -0.80
            and m15 < -1.20
            and not reversal
        )

        distribution = (
            buyer_pressure < 57.0
            and spot_ratio >= 3.0
            and m5 < -0.30
        )

        bad_breakout_chase = (
            live_change > 0.90
            or m5 > 1.80
            or breakout_distance < -0.05
        )

        score = 0
        reasons = []

        if very_low:
            score += 20
            reasons.append("🟦 Çok güçlü yerel dip bölgesi")
        elif low_zone:
            score += 16
            reasons.append("🟦 Dip/birikim bölgesi")
        elif near_low:
            score += 10
            reasons.append("🟦 Dip bölgesine yakın")

        if base_formed:
            score += 10
            reasons.append("🧱 Dip tabanı oluşuyor")

        if ma_squeeze:
            score += 12
            reasons.append(f"📐 MA sıkışması (%{ma_diff:.2f})")

        if ma_turning_up:
            score += 3
            reasons.append("📈 Kısa MA yukarı dönüyor")

        if volatility_compression:
            score += 5
            reasons.append("🤏 Volatilite daralıyor")

        if silent_accumulation:
            score += 12
            reasons.append("🤫 Sessiz birikim tespit edildi")

        if spot_ratio >= 4.0:
            score += 12
            reasons.append(
                f"🐋 Spot para girişi çok güçlü ({spot_ratio:.2f}x)"
            )
        elif spot_ratio >= 2.5:
            score += 9
            reasons.append(
                f"🐋 Spot para girişi güçlü ({spot_ratio:.2f}x)"
            )
        elif spot_ratio >= 2.0:
            score += 6
            reasons.append(
                f"📈 Spot akışı başladı ({spot_ratio:.2f}x)"
            )

        if spot_leading:
            score += 4
            reasons.append("🐋 Spot akışı futures'tan önce geliyor")

        if buyer_pressure >= 80:
            score += 12
            reasons.append(
                f"🟢 Çok güçlü alıcı baskısı (%{buyer_pressure:.1f})"
            )
        elif buyer_pressure >= 72:
            score += 9
            reasons.append(
                f"🟢 Güçlü alıcı baskısı (%{buyer_pressure:.1f})"
            )
        elif buyer_pressure >= 64:
            score += 6
            reasons.append(
                f"🟢 Pozitif alıcı baskısı (%{buyer_pressure:.1f})"
            )

        if higher_low:
            score += 4
            reasons.append("📐 Higher-Low oluştu")

        if break_high:
            score += 4
            reasons.append("💥 Önceki tepe aşıldı")

        if wick_rejection:
            score += 4
            reasons.append("🛡️ Dipte satışlar karşılandı")

        if very_near_breakout:
            score += 8
            reasons.append(
                f"🎯 Kırılım çok yakın (%{breakout_distance:.2f})"
            )
        elif near_breakout:
            score += 5
            reasons.append(
                f"🎯 Kırılım bölgesine yaklaşıyor (%{breakout_distance:.2f})"
            )
        elif resistance_test:
            score += 3
            reasons.append("🎯 Direnç test ediliyor")

        if volume_acceleration >= 3.0:
            score += 4
            reasons.append(
                f"🚀 Hacim ivmesi çok güçlü ({volume_acceleration:.2f}x)"
            )
        elif volume_acceleration >= 1.8:
            score += 3
            reasons.append(
                f"🔥 Hacim ivmesi artıyor ({volume_acceleration:.2f}x)"
            )

        if futures_ratio >= 3.0:
            score += 3
            reasons.append(
                f"⚡ Futures aktivitesi güçlü ({futures_ratio:.2f}x)"
            )
        elif futures_ratio >= 2.0:
            score += 2
            reasons.append(
                f"⚡ Futures destekliyor ({futures_ratio:.2f}x)"
            )

        if oi_change is not None:
            if oi_change >= 0.8:
                score += 4
                reasons.append(f"📈 OI destekli (+%{oi_change:.2f})")
            elif oi_change <= -1.5:
                score -= 4
                reasons.append(f"⚠️ OI geriliyor (%{oi_change:.2f})")

        if falling:
            score -= 15
            reasons.append("⚠️ Düşüş devam ediyor")

        if distribution:
            score -= 20
            reasons.append("⚠️ Dağıtım riski")

        if bad_breakout_chase:
            score -= 10
            reasons.append("⚠️ Hareket fazla ilerledi")

        score = clamp(score)

        dip_structure = very_low or low_zone

        money_flow = (
            spot_ratio >= 2.0
            and buyer_pressure >= 62.0
        )

        preparation_structure = (
            dip_structure
            and (silent_accumulation or ma_squeeze or base_formed)
            and money_flow
            and not falling
            and not distribution
            and not bad_breakout_chase
        )

        breakout_setup = (
            preparation_structure
            and (near_breakout or resistance_test or reversal)
        )

        strong_structure = (
            breakout_setup
            and reversal
            and buyer_pressure >= 68.0
            and (spot_ratio >= 2.5 or volume_acceleration >= 2.0)
        )

        very_strong_structure = (
            strong_structure
            and score >= VERY_STRONG_THRESHOLD
            and buyer_pressure >= 75.0
            and spot_ratio >= 2.5
            and (very_near_breakout or break_high)
            and (higher_low or wick_rejection)
            and live_change <= 0.80
            and m5 <= 1.40
        )

        if very_strong_structure:
            status = "VERY_STRONG"
            signal_type = "🚀🚀 ÇOK ÇOK GÜÇLÜ AL"
        elif strong_structure and score >= STRONG_THRESHOLD:
            status = "STRONG"
            signal_type = "🟢 GÜÇLÜ AL"
        elif preparation_structure and score >= PREPARE_THRESHOLD:
            status = "PREPARE"
            signal_type = "🔵 HAZIRLIK AL"
        else:
            status = "PASS"
            signal_type = "⚪ PASS"

        return {
            "status": status,
            "type": signal_type,
            "symbol": symbol,
            "score": score,
            "price": price,
            "location": location,
            "spot_ratio": spot_ratio,
            "futures_ratio": futures_ratio,
            "trade_ratio": trade_ratio,
            "buyer_pressure": buyer_pressure,
            "live_change": live_change,
            "m5": m5,
            "m15": m15,
            "m30": m30,
            "ma_squeeze": ma_squeeze,
            "ma_diff": ma_diff,
            "silent_accumulation": silent_accumulation,
            "base_formed": base_formed,
            "volatility_compression": volatility_compression,
            "volume_acceleration": volume_acceleration,
            "breakout_distance": breakout_distance,
            "near_breakout": near_breakout,
            "very_near_breakout": very_near_breakout,
            "higher_low": higher_low,
            "break_high": break_high,
            "wick_rejection": wick_rejection,
            "reversal": reversal,
            "spot_leading": spot_leading,
            "oi_change": oi_change,
            "reasons": reasons
        }

    except Exception as e:
        log.debug("%s analiz hatasi: %s", symbol, e)
        return {"status": "error"}


def message(r):
    oi_text = (
        "veri bekleniyor"
        if r["oi_change"] is None
        else f"%{r['oi_change']:.2f}"
    )

    if r["status"] == "VERY_STRONG":
        header = "🐋 BALİNA RADARI V16\n🚀🚀 ÇOK ÇOK GÜÇLÜ AL"
        footer = "🔥 Dip yapısı + para girişi + kırılım hazırlığı çok güçlü."
    elif r["status"] == "STRONG":
        header = "🐋 BALİNA RADARI V16\n🟢 GÜÇLÜ AL"
        footer = "🎯 Dip yapısı tamamlanıyor ve yukarı kırılım teyidi güçleniyor."
    else:
        header = "🐋 BALİNA RADARI V16\n🔵 HAZIRLIK AL"
        footer = "👁️ Erken aşama. Dip/birikim yapısı oluşuyor, kırılım henüz tamamlanmadı."

    reasons = "\n".join(f"• {x}" for x in r["reasons"])

    return (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 Fiyat: {r['price']:.8g}\n"
        f"🏆 GÜÇ SKORU: {r['score']}/100\n\n"

        "📍 DİP KONUMU\n"
        f"• Son 30m konumu: %{r['location']:.1f}\n"
        f"• Dip tabanı: {'✅ VAR' if r['base_formed'] else '❌ YOK'}\n\n"

        "🐋 PARA AKIŞI\n"
        f"• Spot hacim: {r['spot_ratio']:.2f}x\n"
        f"• Futures hacim: {r['futures_ratio']:.2f}x\n"
        f"• İşlem sayısı: {r['trade_ratio']:.2f}x\n"
        f"• Alıcı baskısı: %{r['buyer_pressure']:.1f}\n"
        f"• Spot öncü: {'✅' if r['spot_leading'] else '❌'}\n\n"

        "📐 KIRILIM HAZIRLIĞI\n"
        f"• MA sıkışması: {'✅' if r['ma_squeeze'] else '❌'} "
        f"(%{r['ma_diff']:.2f})\n"
        f"• Sessiz birikim: {'✅' if r['silent_accumulation'] else '❌'}\n"
        f"• Volatilite daralması: {'✅' if r['volatility_compression'] else '❌'}\n"
        f"• Kırılıma mesafe: %{r['breakout_distance']:.2f}\n"
        f"• Hacim ivmesi: {r['volume_acceleration']:.2f}x\n\n"

        "📈 PRICE ACTION\n"
        f"• Higher-Low: {'✅' if r['higher_low'] else '❌'}\n"
        f"• Tepe kırılımı: {'✅' if r['break_high'] else '❌'}\n"
        f"• Satış reddi: {'✅' if r['wick_rejection'] else '❌'}\n\n"

        "⚡ MOMENTUM\n"
        f"• Canlı 1m: {r['live_change']:+.2f}%\n"
        f"• 5m: {r['m5']:+.2f}%\n"
        f"• 15m: {r['m15']:+.2f}%\n"
        f"• OI: {oi_text}\n\n"

        "🔎 NEDEN SİNYAL?\n"
        f"{reasons}\n\n"
        f"{footer}\n"
        "⚠️ Teknik filtredir; risk yönetimi sana aittir."
    )


def scan():
    start = time.time()

    spot = tickers(SPOT)
    futures = tickers(FUT)

    if not spot or not futures:
        log.warning("Ticker verisi alınamadı.")
        return True

    symbols = candidates(spot, futures)
    signals = []
    stats = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        jobs = [
            executor.submit(analyze, symbol)
            for symbol in symbols
        ]

        for job in as_completed(jobs):
            result = job.result()
            status = result.get("status", "error")
            stats[status] = stats.get(status, 0) + 1

            if status in ("VERY_STRONG", "STRONG", "PREPARE"):
                signals.append(result)

    priority = {
        "VERY_STRONG": 3,
        "STRONG": 2,
        "PREPARE": 1
    }

    signals.sort(
        key=lambda x: (
            priority.get(x["status"], 0),
            x["score"],
            x["spot_ratio"]
        ),
        reverse=True
    )

    sent = 0

    for result in signals[:MAX_SIGNALS]:
        symbol = result["symbol"]

        if DBS.cooldown(symbol):
            continue

        if telegram(message(result)):
            DBS.sent(symbol, result["score"])
            sent += 1

        time.sleep(0.5)

    elapsed = time.time() - start
    errors = stats.get("error", 0)
    total = max(1, len(symbols))

    log.info(
        "🐋 V16 | Aday:%d | 🚀ÇOK GÜÇLÜ:%d | 🟢GÜÇLÜ:%d | "
        "🔵HAZIRLIK:%d | Geç:%d | Zayıf:%d | Hata:%d | "
        "Gönder:%d | Süre:%.1fs",
        len(symbols),
        stats.get("VERY_STRONG", 0),
        stats.get("STRONG", 0),
        stats.get("PREPARE", 0),
        stats.get("late", 0),
        stats.get("weak", 0),
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
    return "🐋 Balina Radarı V16 Breakout Predictor Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V16",
        "prepare_threshold": PREPARE_THRESHOLD,
        "strong_threshold": STRONG_THRESHOLD,
        "very_strong_threshold": VERY_STRONG_THRESHOLD
    }


def loop():
    log.info("🐋 BALİNA RADARI V16 başlatılıyor...")

    if TOKEN and CHAT:
        telegram(
            "🐋 BALİNA RADARI V16 AKTİF\n\n"
            "🔵 Dip hazırlık taraması\n"
            "🤫 Sessiz birikim\n"
            "🐋 Spot para akışı\n"
            "📐 MA + volatilite sıkışması\n"
            "🎯 Breakout Predictor\n"
            "🟢 Güçlü Al\n"
            "🚀🚀 Çok Çok Güçlü Al\n"
            "🚫 Geç kalmış hareket filtresi"
        )

    while True:
        started = time.time()

        try:
            backoff = scan()
        except Exception:
            log.exception("Tarama döngüsü hatası")
            backoff = True

        elapsed = time.time() - started

        if backoff:
            wait = max(180, SCAN_INTERVAL * 3)
            log.warning(
                "🛑 Koruma beklemesi: %d saniye",
                wait
            )
            time.sleep(wait)
        else:
            time.sleep(
                max(1, SCAN_INTERVAL - elapsed)
            )


Thread(
    target=loop,
    daemon=True,
    name="balina-v16"
).start()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        use_reloader=False
    )
