
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
# 🐋 BALİNA RADARI V12 — PRICE ACTION & FLOW EDITION
# ============================================================
# Yenilikler:
# 1. Price Action Dönüş Teyidi (Higher-Low + Tepe Kırılımı)
# 2. Düzleştirilmiş Alıcı Baskısı (Son 3-5 mum ortalaması)
# 3. Hacim Tuzağı / Dağıtım Engeli -> 🟡 WATCH Modu
# 4. Normalize Hacim İvmesi (Aşırı spike törpüleme)
# 5. Disiplinli OI (Veri eksikse skor maks 70 ile sınırlandırılır)
# 6. Üçlü Teyit Mekanizması (Hacim + Alıcı + Fiyat Dönüşü)
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

SIGNAL_THRESHOLD = int(os.getenv("SIGNAL_THRESHOLD", "80"))
WATCH_THRESHOLD = int(os.getenv("WATCH_THRESHOLD", "65"))
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "3"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))

TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v12.db")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {
    "BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT",
    "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BUSDUSDT"
}

# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)

log = logging.getLogger("balina-v12")


# ============================================================
# SESSION
# ============================================================

def build_session():
    retry_kwargs = dict(
        total=2, connect=2, read=2, backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False
    )
    try:
        retry = Retry(allowed_methods=["GET", "POST"], **retry_kwargs)
    except TypeError:
        retry = Retry(method_whitelist=["GET", "POST"], **retry_kwargs)

    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "BalinaRadari-V12/1.0"})
    return s

S = build_session()


# ============================================================
# API & TELEGRAM
# ============================================================

def api(base, path, params=None):
    r = S.get(base + path, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def telegram(text):
    if not TOKEN or not CHAT: return False
    try:
        r = S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT, "text": text}, timeout=TIMEOUT)
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        log.error("Telegram hatası: %s", e)
        return False


# ============================================================
# BINANCE DATA
# ============================================================

def tickers(base):
    try:
        path = "/api/v3/ticker/24hr" if base == SPOT else "/fapi/v1/ticker/24hr"
        return api(base, path)
    except Exception as e:
        log.error("Ticker hatası: %s", e)
        return []

def klines(base, symbol, interval, limit):
    try:
        path = "/api/v3/klines" if base == SPOT else "/fapi/v1/klines"
        return api(base, path, {"symbol": symbol, "interval": interval, "limit": limit})
    except Exception:
        return []

def open_interest(symbol):
    try:
        data = api(FUT, "/fapi/v1/openInterest", {"symbol": symbol})
        return float(data["openInterest"])
    except Exception:
        return None


# ============================================================
# HELPERS
# ============================================================

def pct(a, b):
    if a is None or b is None or a <= 0: return 0.0
    return ((b - a) / a) * 100.0

def clamp(v):
    return max(0, min(100, int(round(v))))


# ============================================================
# DATABASE
# ============================================================

class DB:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        with self.lock, sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY, sent REAL, score REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY, value REAL, ts REAL)")

    def get_oi(self, symbol):
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value, ts FROM oi WHERE symbol=?", (symbol,)).fetchone()
        if not row or (time.time() - row[1] > SCAN_INTERVAL * 5): return None
        return float(row[0])

    def put_oi(self, symbol, value):
        if value is None: return
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO oi(symbol,value,ts) VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, ts=excluded.ts", (symbol, value, time.time()))

    def cooldown(self, symbol):
        with self.lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT sent FROM state WHERE symbol=?", (symbol,)).fetchone()
        return bool(row and (time.time() - row[0] < COOLDOWN))

    def sent(self, symbol, score):
        with self.lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO state(symbol,sent,score) VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent, score=excluded.score", (symbol, time.time(), score))

DBS = DB(DB_PATH)


# ============================================================
# ADAY FİLTRESİ (Likidite & Aşırı Yükseliş Kontrolü)
# ============================================================

def candidates(spot, futures):
    futures_map = {x.get("symbol"): x for x in futures}
    result = []
    for item in spot:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT") or symbol in EXCLUDED: continue
        if any(symbol.endswith(x) for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")): continue
        
        future = futures_map.get(symbol)
        if not future: continue

        try:
            spot_vol = float(item.get("quoteVolume", 0))
            fut_vol = float(future.get("quoteVolume", 0))
            daily_change = float(item.get("priceChangePercent", 0))

            if spot_vol < MIN_VOLUME or fut_vol < MIN_VOLUME: continue
            if daily_change > 16.0: continue # Son 24 saatte %16+ patlamış coini ele

            result.append(symbol)
        except (TypeError, ValueError):
            continue
    return result


# ============================================================
# ANALİZ MOTORU (V12 Price Action & Flow)
# ============================================================

def analyze(symbol):
    try:
        spot_1m = klines(SPOT, symbol, "1m", 35)
        futures_1m = klines(FUT, symbol, "1m", 35)
        spot_5m = klines(SPOT, symbol, "5m", 12)

        if len(spot_1m) < 30 or len(futures_1m) < 30 or len(spot_5m) < 8:
            return {"status": "insufficient"}

        # ----------------------------------------------------
        # 1. CANLI MUM VE MOMENTUM
        # ----------------------------------------------------
        live = spot_1m[-1]
        price = float(live[4])
        live_open = float(live[1])
        live_low = float(live[3])
        live_change = pct(live_open, price)

        closes_5m = [float(x[4]) for x in spot_5m]
        momentum_5m = pct(closes_5m[-2], price)
        momentum_15m = pct(closes_5m[-4], price) if len(closes_5m) >= 4 else 0.0

        # PUMP SONRASI ENGEL
        if momentum_5m > 2.8 or momentum_15m > 4.8 or live_change > 1.2:
            return {"status": "late"}
        if live_change < -1.5 or momentum_5m < -3.0:
            return {"status": "weak"}

        # ----------------------------------------------------
        # 2. GERÇEK DÖNÜŞ TEYİDİ (PRICE ACTION)
        # ----------------------------------------------------
        prev1_low = float(spot_1m[-2][3])
        prev2_low = float(spot_1m[-3][3])
        prev1_high = float(spot_1m[-2][2])
        prev1_close = float(spot_1m[-2][4])

        m1 = pct(prev1_close, price)
        higher_low = (live_low >= prev1_low) or (prev1_low >= prev2_low)
        break_prev_high = (price >= prev1_high)

        # Fiyat Dönüşü Onayı: Dip yapmama/yükselen dip + tepe ihlali + yönün artıya dönmesi
        price_reversal = (m1 >= -0.05) and (momentum_5m >= -0.3) and (higher_low or break_prev_high)

        # ----------------------------------------------------
        # 3. DÜZLEŞTİRİLMİŞ ALICI BASKISI (Son 3 Mum)
        # ----------------------------------------------------
        buy_vol_3m = sum(float(x[10]) for x in spot_1m[-3:])
        tot_vol_3m = sum(float(x[7]) for x in spot_1m[-3:])
        smoothed_bp = (buy_vol_3m / tot_vol_3m * 100.0) if tot_vol_3m > 0 else 50.0

        # ----------------------------------------------------
        # 4. HACİM VE İŞLEM SAYISI ANALİZİ (NORMALİZE)
        # ----------------------------------------------------
        spot_closed = spot_1m[:-1]
        futures_closed = futures_1m[:-1]

        spot_volumes = [float(x[7]) for x in spot_1m]
        futures_volumes = [float(x[7]) for x in futures_1m]
        trade_counts = [float(x[8]) for x in spot_1m]

        avg_spot = sum(float(x[7]) for x in spot_closed[-18:]) / 18.0
        avg_futures = sum(float(x[7]) for x in futures_closed[-18:]) / 18.0
        avg_trades = sum(float(x[8]) for x in spot_closed[-18:]) / 18.0

        if avg_spot <= 0 or avg_futures <= 0 or avg_trades <= 0:
            return {"status": "insufficient"}

        recent_spot = sum(spot_volumes[-3:]) / 3.0
        recent_futures = sum(futures_volumes[-3:]) / 3.0
        recent_trades = sum(trade_counts[-3:]) / 3.0

        spot_ratio = recent_spot / avg_spot
        futures_ratio = recent_futures / avg_futures
        trade_ratio = recent_trades / avg_trades

        # Hacim İvmesi
        prev_spot_acc = sum(spot_volumes[-6:-3]) / 3.0
        vol_accel = (recent_spot / prev_spot_acc) if prev_spot_acc > 0 else 1.0

        # Extreme Spike Törpüleme (Max 8.0x etkili)
        norm_spot = min(spot_ratio, 8.0)
        norm_fut = min(futures_ratio, 8.0)

        # ----------------------------------------------------
        # 5. PUANLAMA
        # ----------------------------------------------------
        score = 0
        reasons = []

        if norm_spot >= 4.0: score += 18; reasons.append(f"🔥 Spot hacim güçlü ({spot_ratio:.2f}x)")
        elif norm_spot >= 2.5: score += 12; reasons.append(f"📈 Spot hacim artıyor ({spot_ratio:.2f}x)")

        if norm_fut >= 4.0: score += 17; reasons.append(f"⚡ Vadeli hacim güçlü ({futures_ratio:.2f}x)")
        elif norm_fut >= 2.5: score += 12; reasons.append(f"📊 Vadeli aktivitesi artıyor ({futures_ratio:.2f}x)")

        if trade_ratio >= 2.5: score += 16; reasons.append(f"🤖 İşlem sayısı patlıyor ({trade_ratio:.2f}x)")
        elif trade_ratio >= 1.5: score += 10; reasons.append(f"📈 İşlem sayısı güçlü ({trade_ratio:.2f}x)")

        if smoothed_bp >= 72.0: score += 18; reasons.append(f"🐋 Düzleştirilmiş alıcı baskısı çok güçlü (%{smoothed_bp:.1f})")
        elif smoothed_bp >= 62.0: score += 12; reasons.append(f"🟢 Alıcı baskısı pozitif (%{smoothed_bp:.1f})")

        if price_reversal:
            score += 15
            reasons.append("🎯 Fiyat dönüş teyidi (Higher-Low / Tepe Kırılımı)")

        if 0.05 <= live_change <= 0.60:
            score += 10
            reasons.append(f"🟢 Fiyat erken aşamada (+%{live_change:.2f})")

        # ----------------------------------------------------
        # 6. ÜÇLÜ TEYİT KONTROLÜ & OI DİSİPLİNİ
        # ----------------------------------------------------
        has_vol = (spot_ratio >= 2.0 or futures_ratio >= 2.0 or trade_ratio >= 1.5)
        has_bp = (smoothed_bp >= 60.0)
        has_pa = price_reversal

        oi_change = None
        if score >= WATCH_THRESHOLD:
            now_oi = open_interest(symbol)
            old_oi = DBS.get_oi(symbol)
            if old_oi is not None and now_oi is not None:
                oi_change = pct(old_oi, now_oi)
                if oi_change >= 0.8:
                    score += 7
                    reasons.append(f"📈 OI destekli (+%{oi_change:.2f})")
                elif oi_change <= -1.0:
                    score -= 5
            DBS.put_oi(symbol, now_oi)

        # OI VERİSİ YOKSA MKS SKOR 70 İLE SINIRLANDIRILIR
        if oi_change is None:
            score = min(score, 70)

        score = clamp(score)

        # ----------------------------------------------------
        # 7. DURUM SINIFLANDIRMASI (LONG / WATCH / PASS)
        # ----------------------------------------------------
        # 🟢 LONG: Yüksek skor + Üçlü teyit (Hacim + Alıcı + Fiyat Dönüşü)
        if score >= SIGNAL_THRESHOLD and has_vol and has_bp and has_pa:
            return {
                "status": "LONG", "symbol": symbol, "type": "🟢 LONG (İŞLEM SİNYALİ)",
                "score": score, "price": price, "spot_ratio": spot_ratio,
                "futures_ratio": futures_ratio, "trade_ratio": trade_ratio,
                "smoothed_bp": smoothed_bp, "live_change": live_change,
                "momentum_5m": momentum_5m, "vol_accel": vol_accel,
                "oi": oi_change, "reasons": reasons
            }

        # 🟡 WATCH: Hacim ve Alıcı Var AMA Fiyat Dönüşü Yok VEYA Düşüyor (Dağıtım/Emilim Riski)
        elif (score >= WATCH_THRESHOLD or (has_vol and has_bp)) and (spot_ratio >= 2.5 or futures_ratio >= 2.5):
            if not has_pa:
                reasons.append("⚠️ Hacim var fakat henüz fiyat dönüş teyidi bekleniyor")
            return {
                "status": "WATCH", "symbol": symbol, "type": "🟡 WATCH (TAKİP LİSTESİ)",
                "score": score, "price": price, "spot_ratio": spot_ratio,
                "futures_ratio": futures_ratio, "trade_ratio": trade_ratio,
                "smoothed_bp": smoothed_bp, "live_change": live_change,
                "momentum_5m": momentum_5m, "vol_accel": vol_accel,
                "oi": oi_change, "reasons": reasons
            }

        else:
            return {"status": "PASS", "score": score}

    except Exception as e:
        log.debug("%s analiz hatası: %s", symbol, e)
        return {"status": "error"}


# ============================================================
# TELEGRAM MESAJ FORMATI
# ============================================================

def message(r):
    oi_text = "veri bekleniyor (Skor Sınırlandı)" if r["oi"] is None else f"%{r['oi']:.2f}"
    
    if r["status"] == "LONG":
        header = "🐋 BALİNA RADARI V12 — 🟢 LONG SİNYALİ"
        warning = "🎯 Üçlü teyit (Hacim + Alıcı + Fiyat Dönüşü) sağlandı. Erken aşamadır."
    else:
        header = "🐋 BALİNA RADARI V12 — 🟡 TAKİP LİSTESİ"
        warning = "👁️ Hacim ve balina aktivitesi var ancak fiyat dönüşü henüz tamamlanmadı. Acele etmeyin."

    return (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 {r['type']}\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 Anlık Fiyat: {r['price']:.8g}\n"
        f"🏆 SCORE: {r['score']}/100\n\n"
        "📊 AKIŞ VE ALICI\n"
        f"• Spot Hacim: {r['spot_ratio']:.2f}x\n"
        f"• Futures Hacim: {r['futures_ratio']:.2f}x\n"
        f"• İşlem Sayısı: {r['trade_ratio']:.2f}x\n"
        f"• Düzleştirilmiş Alıcı (3m): %{r['smoothed_bp']:.1f}\n\n"
        "📈 FİYAT HAREKETİ\n"
        f"• Canlı 1m: +%{r['live_change']:.2f}\n"
        f"• 5m Momentum: +%{r['momentum_5m']:.2f}\n"
        f"• Hacim İvmesi: {r['vol_accel']:.2f}x\n"
        f"• OI Değişimi: {oi_text}\n\n"
        "🔎 TEYİT KONTROLLERİ\n"
        + "\n".join("• " + x for x in r["reasons"])
        + f"\n\n⚠️ {warning}"
    )


# ============================================================
# TARAMA DÖNGÜSÜ
# ============================================================

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
        jobs = [executor.submit(analyze, symbol) for symbol in symbols]
        for job in as_completed(jobs):
            result = job.result()
            status = result.get("status", "error")
            stats[status] = stats.get(status, 0) + 1
            if status in ("LONG", "WATCH"):
                signals.append(result)

    # Önce LONG sinyallerini, sonra en yüksek skorlu WATCH sinyallerini sırala
    signals.sort(key=lambda x: (x["status"] == "LONG", x["score"]), reverse=True)

    sent = 0
    for result in signals[:MAX_SIGNALS]:
        symbol = result["symbol"]
        if DBS.cooldown(symbol): continue
        if telegram(message(result)):
            DBS.sent(symbol, result["score"])
            sent += 1
        time.sleep(0.5)

    elapsed = time.time() - start
    errors = stats.get("error", 0)
    total = max(1, len(symbols))

    log.info(
        "🐋 V12 | Aday:%d | LONG:%d | WATCH:%d | Geç:%d | Hata:%d | Süre:%.1fs",
        len(symbols), stats.get("LONG", 0), stats.get("WATCH", 0),
        stats.get("late", 0), errors, elapsed
    )

    if (errors / total > 0.30) or (elapsed > SCAN_INTERVAL * 1.25):
        return True
    return False


# ============================================================
# FLASK & LOOP
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🐋 Balina Radarı V12 Price Action Edition Aktif!"

@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V12",
        "signal_threshold": SIGNAL_THRESHOLD,
        "watch_threshold": WATCH_THRESHOLD
    }

def loop():
    log.info("🐋 BALİNA RADARI V12 başlatılıyor...")
    if TOKEN and CHAT:
        telegram(
            "🐋 BALİNA RADARI V12 AKTİF\n\n"
            "🎯 Price Action Dönüş Teyidi\n"
            "🟢 LONG / 🟡 WATCH Ayrımı\n"
            "📊 Düzleştirilmiş Taker Buy/Sell\n"
            "🛡️ Hacim Tuzağı & Emilim Engeli\n"
            "🚫 Pump Yapmış Coinleri Eler"
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
            log.warning("🛑 Koruma beklemesi: %d saniye", wait)
            time.sleep(wait)
        else:
            time.sleep(max(1, SCAN_INTERVAL - elapsed))

Thread(target=loop, daemon=True, name="balina-v12").start()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        use_reloader=False
)
  
