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
# 🐋 BALİNA RADARI V15 — BOTTOM LAUNCHER EDITION
# ============================================================
# Öne Çıkan Yenilikler:
# 1. 🤏 MA Sıkışması (Volatility Compression / Squeeze):
#    MA7 ve MA30 çizgilerinin birbirine yapışıp yayın gerilmesini tespit eder.
# 2. 🤫 Sessiz Birikim (Silent Volume Divergence):
#    Fiyat henüz hareket etmezken (% -0.35 ile +0.45 arası) arkadan giren
#    büyük spot hacmi yakalar.
# 3. 🛡️ Alt Fitil / İğne Tepkisi (Wick Rejection):
#    Satışların dipte balina tarafından karşılandığını doğrular.
# 4. 🎯 Özel İsmail Güçlü Alım Alarmı:
#    Dip birikimi tamamlanan tahtalarda doğrudan nokta atışı uyarı gönderir.
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "6"))

STRONG_THRESHOLD = int(os.getenv("STRONG_THRESHOLD", "82"))
CANDIDATE_THRESHOLD = int(os.getenv("CANDIDATE_THRESHOLD", "72"))

MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "2"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v15.db")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {
    "BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT",
    "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BUSDUSDT"
}

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("balina-v15")


# ============================================================
# NETWORK & SESSION
# ============================================================

def build_session():
    kw = dict(
        total=2, connect=2, read=2, backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False
    )
    try:
        r = Retry(allowed_methods=["GET", "POST"], **kw)
    except TypeError:
        r = Retry(method_whitelist=["GET", "POST"], **kw)

    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=r)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "BalinaRadari-V15/1.0"})
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
    if not TOKEN or not CHAT:
        log.warning("Telegram ayarları eksik.")
        return False
    try:
        r = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT, "text": text},
            timeout=TIMEOUT
        )
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        log.error("Telegram hatası: %s", e)
        return False

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
    except Exception as e:
        log.debug("%s %s kline hatası: %s", symbol, interval, e)
        return []

def open_interest(symbol):
    try:
        data = api(FUT, "/fapi/v1/openInterest", {"symbol": symbol})
        return float(data["openInterest"])
    except Exception:
        return None


# ============================================================
# YARDIMCI MATEMATİKSEL FONKSİYONLAR
# ============================================================

def pct(a, b):
    return ((b - a) / a * 100.0) if a and a > 0 and b is not None else 0.0

def clamp(x):
    return max(0, min(100, int(round(x))))

def average(values):
    return sum(values) / len(values) if values else 0.0

def rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = average(gains[-period:])
    al = average(losses[-period:])
    if al <= 0: return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


# ============================================================
# DATABASE
# ============================================================

class DB:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        with self.lock, sqlite3.connect(path) as d:
            d.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY, sent REAL, score REAL)")
            d.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY, value REAL, ts REAL)")

    def get_oi(self, s):
        with self.lock, sqlite3.connect(self.path) as d:
            r = d.execute("SELECT value, ts FROM oi WHERE symbol=?", (s,)).fetchone()
        if not r or (time.time() - r[1] > SCAN_INTERVAL * 5): return None
        return float(r[0])

    def put_oi(self, s, v):
        if v is None: return
        with self.lock, sqlite3.connect(self.path) as d:
            d.execute("INSERT INTO oi VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, ts=excluded.ts", (s, v, time.time()))

    def cooldown(self, s):
        with self.lock, sqlite3.connect(self.path) as d:
            r = d.execute("SELECT sent FROM state WHERE symbol=?", (s,)).fetchone()
        return bool(r and (time.time() - r[0] < COOLDOWN))

    def sent(self, s, score):
        with self.lock, sqlite3.connect(self.path) as d:
            d.execute("INSERT INTO state VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent, score=excluded.score", (s, time.time(), score))

DBS = DB(DB_PATH)


# ============================================================
# ADAY FİLTRESİ
# ============================================================

def candidates(st, ft):
    fm = {x.get("symbol"): x for x in ft}
    out = []
    for x in st:
        s = x.get("symbol", "")
        if not s.endswith("USDT") or s in EXCLUDED or any(s.endswith(z) for z in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue
        f = fm.get(s)
        if not f: continue
        try:
            spot_vol = float(x.get("quoteVolume", 0))
            fut_vol = float(f.get("quoteVolume", 0))
            daily_change = float(x.get("priceChangePercent", 0))

            if spot_vol < MIN_VOLUME or fut_vol < MIN_VOLUME: continue
            if daily_change > 16.0: continue # Aşırı yükselmiş coinleri ele

            out.append(s)
        except (TypeError, ValueError):
            continue
    return out


# ============================================================
# ANALİZ MOTORU (V15 Bottom Launcher)
# ============================================================

def analyze(s):
    try:
        sp = klines(SPOT, s, "1m", 48)
        fu = klines(FUT, s, "1m", 36)
        sp5 = klines(SPOT, s, "5m", 18)

        if len(sp) < 35 or len(fu) < 30 or len(sp5) < 10:
            return {"status": "insufficient"}

        live = sp[-1]
        price = float(live[4])
        live_open = float(live[1])
        live_low = float(live[3])

        lc = pct(live_open, price)

        c5 = [float(x[4]) for x in sp5]
        m5 = pct(c5[-2], price)
        m15 = pct(c5[-4], price)
        m30 = pct(c5[-7], price)

        # ----------------------------------------------------
        # 1) GEÇ KALMA VE AŞIRI DÜŞÜŞ KORUMASI
        # ----------------------------------------------------
        if lc > 1.20 or m5 > 2.50 or m15 > 4.50 or m30 > 7.0:
            return {"status": "late"}
        if lc < -2.0 or m5 < -3.5:
            return {"status": "weak"}

        # ----------------------------------------------------
        # 2) MA SIKISMASI (MA7 & MA30 SQUEEZE)
        # ----------------------------------------------------
        closes_1m = [float(x[4]) for x in sp]
        ma7 = average(closes_1m[-7:])
        ma30 = average(closes_1m[-30:])
        ma_diff_pct = abs(ma7 - ma30) / price * 100.0

        ma_squeeze = (ma_diff_pct <= 0.85) # Ortalamalar %0.85'ten daha yakınsa sıkışma var

        # ----------------------------------------------------
        # 3) YEREL DİP BÖLGESİ KONUMU
        # ----------------------------------------------------
        closed_1m = sp[:-1]
        lows_30 = [float(x[3]) for x in closed_1m[-30:]]
        highs_30 = [float(x[2]) for x in closed_1m[-30:]]

        lo, hi = min(lows_30), max(highs_30)
        location = ((price - lo) / (hi - lo) * 100.0) if hi > lo else 50.0

        very_low = location <= 25.0
        near_low = location <= 40.0

        # ----------------------------------------------------
        # 4) PRICE ACTION & İĞNE TEPKİSİ (WICK REJECTION)
        # ----------------------------------------------------
        a, b = sp[-2], sp[-3]
        a_open, a_high, a_low, a_close = float(a[1]), float(a[2]), float(a[3]), float(a[4])
        b_low, b_high = float(b[3]), float(b[2])

        higher_low = (a_low > b_low and live_low >= a_low)
        break_high = (price > a_high)

        body = abs(a_close - a_open)
        lower_wick = min(a_open, a_close) - a_low
        wick_rejection = (lower_wick > 0 and lower_wick >= body * 0.8)

        reversal = (higher_low or break_high or wick_rejection)

        # ----------------------------------------------------
        # 5) HACİM & SESSIZ BİRİKİM (SILENT ACCUMULATION)
        # ----------------------------------------------------
        sc = sp[:-1]
        fc = fu[:-1]

        sv = [float(x[7]) for x in sp]
        fv = [float(x[7]) for x in fu]
        tr = [float(x[8]) for x in sp]

        avs = average([float(x[7]) for x in sc[-18:]])
        avf = average([float(x[7]) for x in fc[-18:]])
        avt = average([float(x[8]) for x in sc[-18:]])

        if min(avs, avf, avt) <= 0: return {"status": "insufficient"}

        sr = average(sv[-3:]) / avs
        fr = average(fv[-3:]) / avf
        trr = average(tr[-3:]) / avt

        # Sessiz Birikim: Fiyat yatay/durğan (-0.35 ile +0.45) ama hacim en az 2.2x
        silent_accum = (-0.35 <= lc <= 0.45) and (sr >= 2.2 or fr >= 2.2)

        # Düzleştirilmiş Alıcı Baskısı (Son 3-5 mum)
        buy3 = sum(float(x[10]) for x in sp[-3:])
        vol3 = sum(float(x[7]) for x in sp[-3:])
        bp3 = (buy3 / vol3 * 100.0) if vol3 > 0 else 50.0

        buy5 = sum(float(x[10]) for x in sp[-5:])
        vol5 = sum(float(x[7]) for x in sp[-5:])
        bp5 = (buy5 / vol5 * 100.0) if vol5 > 0 else 50.0

        bp = bp3 * 0.65 + bp5 * 0.35

        # ----------------------------------------------------
        # 6) STRATEJİ PUANLAMASI
        # ----------------------------------------------------
        score = 0
        reasons = []

        # DİP KONUMU (Max 20 Puan)
        if very_low:
            score += 20; reasons.append("🟦 Yerel dip bölgesi ÇOK güçlü")
        elif near_low:
            score += 14; reasons.append("🟦 Yerel dip/birikim bölgesi")

        # MA SIKISMASI (Max 15 Puan)
        if ma_squeeze:
            score += 15; reasons.append(f"📐 MA Ortalamaları Sıkıştı (%{ma_diff_pct:.2f})")

        # SESSIZ BİRİKİM (Max 15 Puan)
        if silent_accum:
            score += 15; reasons.append("🤫 Sessiz Balina Birikimi (Fiyat Sabit / Hacim Yüksek)")

        # PRICE ACTION & FITIL (Max 18 Puan)
        if higher_low: score += 7; reasons.append("📐 Higher-Low oluştu")
        if break_high: score += 6; reasons.append("💥 Önceki mum tepesi kırıldı")
        if wick_rejection: score += 5; reasons.append("🛡️ Dipte satış iğne ile karşılandı")

        # SPOT HACIM VE AKIS (Max 18 Puan)
        if sr >= 3.5: score += 12; reasons.append(f"🐋 Spot hacmi çok güçlü ({sr:.2f}x)")
        elif sr >= 2.0: score += 8; reasons.append(f"📈 Spot hacmi artıyor ({sr:.2f}x)")

        if bp >= 75.0: score += 10; reasons.append(f"🟢 Alıcı baskısı baskın (%{bp:.1f})")
        elif bp >= 65.0: score += 6; reasons.append(f"🟢 Pozitif alıcı akışı (%{bp:.1f})")

        # ISLEM SAYISI & FUTURES (Max 14 Puan)
        if trr >= 1.8: score += 7; reasons.append(f"🤖 İşlem sayısı arttı ({trr:.2f}x)")
        if fr >= 1.8: score += 7; reasons.append(f"⚡ Futures hacmi destekliyor ({fr:.2f}x)")

        # ----------------------------------------------------
        # 7) CEZA / FİLTRELER
        # ----------------------------------------------------
        falling = (m5 < -0.8 and m15 < -1.2 and not reversal)
        if falling:
            score -= 15; reasons.append("⚠️ Düşüş trendi devam ediyor")

        distribution = (bp < 58.0 and sr >= 3.0 and m5 < -0.3)
        if distribution:
            score -= 20; reasons.append("⚠️ Mal yıkma / Dağıtım riski yüksek")

        score = clamp(score)

        # ----------------------------------------------------
        # 8) AKILLI OI KONTROLÜ
        # ----------------------------------------------------
        oi_change = None
        if score >= CANDIDATE_THRESHOLD - 5:
            now_o = open_interest(s)
            old_o = DBS.get_oi(s)
            if old_o is not None and now_o is not None:
                oi_change = pct(old_o, now_o)
                if oi_change >= 0.8:
                    score = clamp(score + 4)
                    reasons.append(f"📈 OI destekli (+%{oi_change:.2f})")
                elif oi_change <= -1.5:
                    score = clamp(score - 4)
                    reasons.append(f"⚠️ OI geriliyor (%{oi_change:.2f})")
            DBS.put_oi(s, now_o)

        # ----------------------------------------------------
        # 9) YAPISAL STRATEJİ KARARI
        # ----------------------------------------------------
        accumulation = (very_low or near_low) and (silent_accum or ma_squeeze or sr >= 2.0) and bp >= 62.0 and not falling and not distribution
        turning = accumulation and reversal
        early = turning and lc <= 0.80 and m5 <= 1.40

        strong = (score >= STRONG_THRESHOLD) and accumulation and turning and early
        candidate = (score >= CANDIDATE_THRESHOLD) and accumulation and (turning or silent_accum)

        if strong:
            status = "STRONG"
            signal_type = "🟢 DİP DÖNÜŞÜ (GÜÇLÜ ALIM)"
        elif candidate:
            status = "CANDIDATE"
            signal_type = "🟡 DİP BİRİKİM ADAYI"
        else:
            status = "PASS"
            signal_type = "⚪ PASS"

        return {
            "status": status,
            "type": signal_type,
            "symbol": s,
            "score": score,
            "price": price,
            "sr": sr,
            "fr": fr,
            "trr": trr,
            "bp": bp,
            "lc": lc,
            "m5": m5,
            "ma_squeeze": ma_squeeze,
            "silent_accum": silent_accum,
            "ma_diff_pct": ma_diff_pct,
            "oi": oi_change,
            "reasons": reasons
        }

    except Exception as e:
        log.debug("%s analiz hatası: %s", s, e)
        return {"status": "error"}


# ============================================================
# TELEGRAM MESAJ FORMATI
# ============================================================

def message(r):
    oi_text = "veri bekleniyor" if r["oi"] is None else f"%{r['oi']:.2f}"
    
    if r["status"] == "STRONG":
        header = "🐋 BALİNA RADARI — 🟢 GÜÇLÜ ALIM BÖLGESİ"
        warning = "⚠️ İsmail, bu tahtada patlama öncesi dip birikimi tamamlandı! Ortalamalar sıkıştı ve akıllı para girişi başladı."
    else:
        header = "🐋 BALİNA RADARI — 🟡 DİP BİRİKİM ADAYI"
        warning = "👁️ Tahtada sessiz hacim/birikim var ancak tam dönüş mumu henüz oluşmadı. İzlemeye alalım."

    return (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 {r['type']}\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 Anlık Fiyat: {r['price']:.8g}\n"
        f"🏆 DİP SKORU: {r['score']}/100\n\n"
        "📊 DİP & BİRİKİM SİNYALLERİ\n"
        f"• Ortalamalar Sıkıştı: {'✅ EVET (%' + f'{r[\"ma_diff_pct\"]:.2f}' + ')' if r['ma_squeeze'] else '❌ HAYIR'}\n"
        f"• Sessiz Birikim: {'✅ VAR' if r['silent_accum'] else '❌ YOK'}\n"
        f"• Spot Hacim: {r['sr']:.2f}x\n"
        f"• Futures Hacim: {r['fr']:.2f}x\n"
        f"• Düzleştirilmiş Alıcı: %{r['bp']:.1f}\n\n"
        "📈 FİYAT HAREKETİ\n"
        f"• Canlı 1m: +%{r['lc']:.2f}\n"
        f"• 5m Momentum: +%{r['m5']:.2f}\n"
        f"• OI Değişimi: {oi_text}\n\n"
        "🔎 TEYİT DETAYLARI\n"
        + "\n".join("• " + x for x in r["reasons"])
        + f"\n\n{warning}"
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
            if status in ("STRONG", "CANDIDATE"):
                signals.append(result)

    signals.sort(key=lambda x: (x["status"] == "STRONG", x["score"]), reverse=True)

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
        "🐋 V15 | Aday:%d | GÜÇLÜ ALIM:%d | ADAY:%d | Geç:%d | Hata:%d | Süre:%.1fs",
        len(symbols), stats.get("STRONG", 0), stats.get("CANDIDATE", 0),
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
    return "🐋 Balina Radarı V15 Bottom Launcher Edition Aktif!"

@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V15",
        "strong_threshold": STRONG_THRESHOLD,
        "candidate_threshold": CANDIDATE_THRESHOLD
    }

def loop():
    log.info("🐋 BALİNA RADARI V15 başlatılıyor...")
    if TOKEN and CHAT:
        telegram(
            "🐋 BALİNA RADARI V15 AKTİF\n\n"
            "📐 MA Ortalamaları Sıkışma Tespiti\n"
            "🤫 Sessiz Balina Birikim Filtresi\n"
            "🛡️ Dip İğne / Alt Fitil Onayı\n"
            "🎯 Doğrudan İsmail Özel Alım Uyarısı"
        )

    while True:
        started = time.time()
        try:
            backoff = scan()
        except Exception:
           
