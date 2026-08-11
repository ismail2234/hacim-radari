
import os, time, sqlite3, logging, sys
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask

# ============================================================
# 🎯 BALİNA RADARI V9 - SNIPER EDITION (1m + Live Candle)
#
# BU DOSYA: senin V9'unun ÜZERİNE, SADECE rate-limit / ban
# riskini azaltmaya yönelik 4 değişiklik eklenmiş hali.
# Skorlama mantığı, mesaj formatı, mimari AYNI kaldı.
#
#   [FIX 1] Open Interest isteği artık "gate'lendi": sadece
#           sr/tr eşiğini geçmiş VE OI bonusu olmadan bile
#           eşiğe yakın adaylarda çekiliyor. Zayıf adaylarda
#           4. istek (futures ağırlığı) hiç atılmıyor.
#   [FIX 2] Devre kesici: bir tarama turunda hata oranı
#           yüksekse (rate-limit belirtisi olabilir), bot
#           kendiliğinden normalden çok daha uzun bekliyor.
#   [FIX 3] Tarama süresi SCAN_INTERVAL'i aşarsa erken uyarı
#           logu — banlanmadan önceki ilk belirtiyi yakalar.
#   [FIX 4] urllib3 Retry uyumluluk fallback'i + "if old and
#           now" -> None kontrolü (önceki versiyonlarla aynı
#           tutarlılık düzeltmesi).
# ============================================================

MIN_VOLUME = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
WORKERS = int(os.getenv("MAX_WORKERS", "8"))
EARLY_SCORE = int(os.getenv("EARLY_SCORE", "75"))
MAX_SIGNALS = int(os.getenv("MAX_SIGNALS_PER_SCAN", "3"))
COOLDOWN = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))
DB_PATH = os.getenv("STATE_DB_PATH", "balina_v9_sniper.db")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# [FIX 1] OI bonusunun skora katabileceği maksimum (mevcut
# skorlamadaki "OI Destekli" dalına bakılarak: +8).
OI_MAX_BONUS = 8

# [FIX 2] Devre kesici ayarları.
ERROR_RATE_BACKOFF_THRESHOLD = float(os.getenv("ERROR_RATE_BACKOFF_THRESHOLD", "0.3"))
ERROR_BACKOFF_MULTIPLIER = int(os.getenv("ERROR_BACKOFF_MULTIPLIER", "5"))

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"
EXCLUDED = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", stream=sys.stdout)
log = logging.getLogger("v9-sniper")


def validate_config():
    if not TOKEN or not CHAT:
        log.warning("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik.")
    log.info(
        "🧮 OI_MAX_BONUS=%d | EARLY_SCORE=%d -> OI sadece score>=%d olan adaylarda çekilecek",
        OI_MAX_BONUS, EARLY_SCORE, EARLY_SCORE - OI_MAX_BONUS,
    )


def build_session():
    # [FIX 4] urllib3 sürümüne göre Retry parametre adı değişir.
    retry_kwargs = dict(
        total=3, connect=3, read=3, backoff_factor=.6,
        status_forcelist=[429, 500, 502, 503, 504], raise_on_status=False,
    )
    try:
        r = Retry(allowed_methods=["GET", "POST"], **retry_kwargs)
    except TypeError:
        r = Retry(method_whitelist=["GET", "POST"], **retry_kwargs)
    s = requests.Session()
    a = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=r)
    s.mount("https://", a); s.mount("http://", a)
    s.headers.update({"User-Agent": "Sniper-V9/1.1"}); return s


S = build_session()


def api(base, path, params=None):
    r = S.get(base + path, params=params, timeout=TIMEOUT); r.raise_for_status(); return r.json()


def telegram(text):
    if not TOKEN or not CHAT:
        log.error("Telegram credential eksik.")
        return False
    try:
        r = S.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT, "text": text}, timeout=TIMEOUT)
        r.raise_for_status(); return bool(r.json().get("ok"))
    except Exception as e: log.error("Telegram: %s", e); return False


def tickers(base):
    try: return api(base, "/api/v3/ticker/24hr" if base == SPOT else "/fapi/v1/ticker/24hr")
    except Exception as e: log.error("Ticker: %s", e); return []


def klines(base, symbol, interval, limit=30):
    try: return api(base, "/api/v3/klines" if base == SPOT else "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    except Exception as e:
        log.debug("%s %s kline hatası: %s", symbol, interval, e)
        return []


def open_interest(symbol):
    try: return float(api(FUT, "/fapi/v1/openInterest", {"symbol": symbol})["openInterest"])
    except Exception as e:
        log.debug("%s OI hatası: %s", symbol, e)
        return None


class DB:
    def __init__(self, path):
        self.path = path; self.lock = Lock()
        with self.lock, sqlite3.connect(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY, sent REAL, score REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY, value REAL, ts REAL)")

    def get_oi(self, s):
        with self.lock, sqlite3.connect(self.path) as c: r = c.execute("SELECT value, ts FROM oi WHERE symbol=?", (s,)).fetchone()
        return None if not r or time.time() - r[1] > SCAN_INTERVAL * 5 else float(r[0])

    def put_oi(self, s, v):
        if v is None: return
        with self.lock, sqlite3.connect(self.path) as c: c.execute("INSERT INTO oi VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, ts=excluded.ts", (s, v, time.time()))

    def cooldown(self, s):
        with self.lock, sqlite3.connect(self.path) as c: r = c.execute("SELECT sent FROM state WHERE symbol=?", (s,)).fetchone()
        return bool(r and time.time() - r[0] < COOLDOWN)

    def sent(self, s, score):
        with self.lock, sqlite3.connect(self.path) as c: c.execute("INSERT INTO state VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent, score=excluded.score", (s, time.time(), score))


DBS = DB(DB_PATH)


def candidates(st, ft):
    fm = {x.get("symbol"): x for x in ft}; out = []
    for x in st:
        s = x.get("symbol", "")
        if not s.endswith("USDT") or s in EXCLUDED or any(s.endswith(z) for z in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")): continue
        f = fm.get(s)
        if not f: continue
        try:
            if float(x.get("quoteVolume", 0)) < MIN_VOLUME or float(f.get("quoteVolume", 0)) < MIN_VOLUME: continue
        except (TypeError, ValueError): continue
        out.append(s)
    return out


def pct(a, b): return (b - a) / a * 100 if a and a > 0 else 0
def clamp(x): return max(0, min(100, int(x)))


def _score_without_oi(sr, fr, tr, bp, live_change):
    """[FIX 1] OI'ye ihtiyaç duymayan puan bileşenleri — bunlar
    zaten çekilmiş kline verisinden hesaplanıyor, ekstra istek
    gerektirmiyor."""
    score = 0; reasons = []

    if sr >= 4: score += 20; reasons.append(f"🚀 Ani Spot Hacmi ({sr:.1f}x)")
    elif sr >= 2: score += 12; reasons.append(f"🔥 Hızlı Spot Girişi ({sr:.1f}x)")

    if fr >= 4: score += 20; reasons.append(f"⚡ Ani Vadeli Hacmi ({fr:.1f}x)")
    elif fr >= 2: score += 12; reasons.append(f"📊 Vadeli Hacmi Artıyor ({fr:.1f}x)")

    if tr >= 3: score += 25; reasons.append(f"🤖 Anormal İşlem Sayısı ({tr:.1f}x) - Balina/Bot Akını")
    elif tr >= 1.5: score += 12; reasons.append(f"📈 İşlem Sayısı İvmeleniyor ({tr:.1f}x)")

    if bp >= 75: score += 20; reasons.append(f"🐋 Agresif Piyasa Alımı (%{bp:.1f})")
    elif bp >= 60: score += 12; reasons.append(f"🟢 Alıcı Baskısı Güçlü (%{bp:.1f})")

    if 0.1 <= live_change <= 1.2: score += 15; reasons.append(f"🎯 Fiyat Henüz Uçmadı (Sadece +%{live_change:.2f})")

    return score, reasons


def analyze(s):
    try:
        sp1 = klines(SPOT, s, "1m", 30)
        fu1 = klines(FUT, s, "1m", 30)
        sp5 = klines(SPOT, s, "5m", 10)

        if len(sp1) < 25 or len(fu1) < 25 or len(sp5) < 5: return {"status": "insufficient"}

        live_1m = sp1[-1]
        live_price = float(live_1m[4])
        live_open = float(live_1m[1])

        live_change = pct(live_open, live_price)
        if live_change > 1.5: return {"status": "late", "score": 0}
        if live_change < -1.0: return {"status": "weak", "score": 0}

        close5 = [float(x[4]) for x in sp5]
        m15 = pct(close5[-4], live_price) if len(close5) >= 4 else 0
        if m15 > 4: return {"status": "late", "score": 0}

        sp1_closed = sp1[:-1]
        fu1_closed = fu1[:-1]

        vol1 = [float(x[7]) for x in sp1]
        fvol1 = [float(x[7]) for x in fu1]
        trades1 = [int(x[8]) for x in sp1]

        avg_vol = sum([float(x[7]) for x in sp1_closed[-15:]]) / 15
        avg_fvol = sum([float(x[7]) for x in fu1_closed[-15:]]) / 15
        avg_trades = sum([int(x[8]) for x in sp1_closed[-15:]]) / 15

        if avg_vol <= 0 or avg_fvol <= 0 or avg_trades <= 0: return {"status": "insufficient"}

        recent_vol = sum(vol1[-3:]) / 3
        recent_fvol = sum(fvol1[-3:]) / 3
        recent_trades = sum(trades1[-3:]) / 3

        sr = recent_vol / avg_vol
        fr = recent_fvol / avg_fvol
        tr = recent_trades / avg_trades

        live_buy = float(live_1m[10])
        live_vol = float(live_1m[7])
        bp = (live_buy / live_vol * 100) if live_vol > 0 else 50

        # [FIX 1] Önce ucuz (istek gerektirmeyen) kontroller.
        # sr/tr kapısını geçemeyen adaylarda OI'ye hiç bakılmıyor.
        base_score, base_reasons = _score_without_oi(sr, fr, tr, bp, live_change)

        if sr < 1.5 or tr < 1.3:
            return {"status": "below_score", "score": clamp(base_score)}

        # OI, skoru en fazla OI_MAX_BONUS kadar artırabilir. Bu
        # eklense bile eşiğe ulaşamayacaksa, isteği hiç atma.
        if base_score + OI_MAX_BONUS < EARLY_SCORE:
            return {"status": "below_score", "score": clamp(base_score)}

        now = open_interest(s)
        old = DBS.get_oi(s)
        # [FIX 4] "if old and now" yerine None kontrolü.
        oi = pct(old, now) if (old is not None and now is not None) else 0
        DBS.put_oi(s, now)

        score = base_score
        reasons = list(base_reasons)
        if old is not None and now is not None and oi >= 1:
            score += 8; reasons.append(f"📈 OI Destekli (+%{oi:.1f})")

        score = clamp(score)

        if score < EARLY_SCORE:
            return {"status": "below_score", "score": score}

        return {
            "status": "signal", "symbol": s, "type": "🎯 SNIPER: GİZLİ BALİNA GİRİŞİ",
            "score": score, "price": live_price, "sr": sr, "fr": fr, "tr": tr, "bp": bp,
            "live_change": live_change, "oi": oi, "reasons": reasons
        }
    except Exception as e: log.debug("%s: %s", s, e); return {"status": "error"}


def message(r):
    return (
        "🎯 BALİNA RADARI V9 - SNIPER\n━━━━━━━━━━━━━━━━━━\n\n"
        f"{r['type']}\n🪙 #{r['symbol']}\n"
        f"💰 Anlık Fiyat: {r['price']:.8g}\n"
        f"🏆 SCORE: {r['score']}/100\n\n"
        "⚡ ANLIK VERİ (Son 3 Dk.)\n"
        f"• İşlem Sayısı Artışı: {r['tr']:.2f}x\n"
        f"• Spot Hacim: {r['sr']:.2f}x\n"
        f"• Futures Hacim: {r['fr']:.2f}x\n"
        f"• Alıcı Baskısı: %{r['bp']:.1f}\n\n"
        "📈 HAREKET DURUMU\n"
        f"• Canlı Mum Değişimi: %{r['live_change']:.2f}\n"
        f"• OI Değişimi: %{r['oi']:.2f}\n\n"
        "🔎 TESPİT DETAYLARI\n" +
        "\n".join("• " + x for x in r["reasons"]) +
        "\n\n⚠️ Keskin Nişancı sinyalidir. Mum dikilmeden önce atılır."
    )


def scan():
    """[FIX 2] Artık bool döner: True = bu turda devre kesici
    tetiklendi, loop() ekstra bekleme uygulamalı."""
    st = tickers(SPOT); ft = tickers(FUT)
    if not st or not ft:
        log.warning("⚠️ Ticker verisi alınamadı — muhtemelen rate-limit, devre kesici tetikleniyor.")
        return True

    cs = candidates(st, ft); signals = []; stats = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in as_completed([ex.submit(analyze, s) for s in cs]):
            r = f.result(); k = r.get("status", "error"); stats[k] = stats.get(k, 0) + 1
            if k == "signal": signals.append(r)

    signals.sort(key=lambda x: x["score"], reverse=True); sent = 0
    for r in signals[:MAX_SIGNALS]:
        if DBS.cooldown(r["symbol"]): continue
        if telegram(message(r)): DBS.sent(r["symbol"], r["score"]); sent += 1
        time.sleep(.4)

    log.info(
        "🎯 SNIPER V9 | Aday:%d | Sinyal:%d | AltPuan:%d | GeçKalmış:%d | Hata:%d",
        len(cs), sent, stats.get("below_score", 0), stats.get("late", 0), stats.get("error", 0),
    )

    # [FIX 2] Devre kesici kontrolü.
    total = len(cs) if cs else 1
    error_rate = stats.get("error", 0) / total
    if error_rate > ERROR_RATE_BACKOFF_THRESHOLD:
        log.error(
            "🛑 Hata oranı %.0f%% (eşik %.0f%%) — muhtemelen rate-limit. Devre kesici tetikleniyor.",
            error_rate * 100, ERROR_RATE_BACKOFF_THRESHOLD * 100,
        )
        return True

    return False


app = Flask(__name__)
@app.route("/")
def home(): return "🎯 Balina Radarı V9 Sniper Aktif!"
@app.route("/health")
def health(): return {"status": "ok", "bot": "Balina Radarı V9 Sniper", "mode": "sniper-live-candle"}


def loop():
    log.info("🎯 BALİNA RADARI V9 SNIPER başlatılıyor...")
    validate_config()
    telegram("🎯 BALİNA RADARI V9 SNIPER AKTİF\n\n⏱️ 1 Dakikalık Mikro Tarama\n🕯️ Canlı Mum Analizi\n🤖 İşlem Sayısı (Trade Count) Takibi\n🚫 Pump Yapmış Coinleri Eler\n🛡️ Rate-Limit Koruması Aktif")

    while True:
        t = time.time()
        backoff = False
        try:
            backoff = scan()
        except Exception:
            log.exception("Tarama hatası")
            backoff = True

        elapsed = time.time() - t

        # [FIX 3] Erken uyarı: tarama SCAN_INTERVAL'i aşıyorsa
        # sistem yetişemiyor demektir — ban öncesi ilk belirti.
        if elapsed > SCAN_INTERVAL * 1.2:
            log.warning(
                "⚠️ Tarama süresi (%.1fs) SCAN_INTERVAL'i (%ds) aştı — "
                "sistem yetişemiyor, bu rate-limit riskinin erken belirtisi olabilir.",
                elapsed, SCAN_INTERVAL,
            )

        if backoff:
            wait = SCAN_INTERVAL * ERROR_BACKOFF_MULTIPLIER
            log.error("🛑 %d saniye ekstra bekleniyor (devre kesici).", wait)
            time.sleep(wait)
        else:
            time.sleep(max(1, SCAN_INTERVAL - elapsed))


Thread(target=loop, daemon=True).start()
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), use_reloader=False)
