import os
import time
import sqlite3
import logging
from dataclasses import dataclass, field
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V5 — SPOT + FUTURES COMPOSITE ENGINE
# (PROFESYONEL YAPI)
#
# V5'in orijinaline göre DEĞİŞENLER:
#   - funding() artık hata durumunda sessizce 0.0 DEĞİL, None
#     döner. Böylece bir API hatası "funding dengeli" diye
#     yanlışlıkla +5 puan üretmiyor.
#   - Open Interest karşılaştırması artık "staleness" kontrollü:
#     çok eski bir referans değerle kıyaslama yapılmıyor.
#   - OI + funding istekleri (sembol başına 2 ekstra HTTP call)
#     sadece "bu aday matematiksel olarak eşiği geçebilir mi"
#     kontrolünden geçenler için atılıyor -> rate-limit riski
#     azalır, NİHAİ SİNYAL SONUCU DEĞİŞMEZ (bkz. aşağıdaki not).
#   - analyze() küçük, tek işi olan fonksiyonlara bölündü.
#   - Bare except'ler kaldırıldı, her hata loglanıyor.
#
# Skorlama ağırlıkları / eşikleri DEĞİŞMEDİ.
#
# ⚠️ En alttaki Thread + __main__ başlatma bloğu bilinçli
# olarak DEĞİŞTİRİLMEDİ.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

@dataclass(frozen=True)
class Config:
    min_volume: float = field(default_factory=lambda: float(os.getenv("MIN_VOLUME_USDT", "1000000")))
    scan_interval: int = field(default_factory=lambda: int(os.getenv("SCAN_INTERVAL", "300")))
    workers: int = field(default_factory=lambda: int(os.getenv("MAX_WORKERS", "8")))
    early: int = field(default_factory=lambda: int(os.getenv("EARLY_SCORE", "68")))
    strong: int = field(default_factory=lambda: int(os.getenv("STRONG_SCORE", "80")))
    whale: int = field(default_factory=lambda: int(os.getenv("WHALE_SCORE", "90")))
    max_signals: int = field(default_factory=lambda: int(os.getenv("MAX_SIGNALS_PER_SCAN", "5")))
    cooldown: int = field(default_factory=lambda: int(os.getenv("SIGNAL_COOLDOWN", "7200")))
    timeout: int = field(default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT", "8")))
    db: str = field(default_factory=lambda: os.getenv("STATE_DB_PATH", "balina_v5.db"))

    # OI referans değeri bu kadar saniyeden eskiyse (varsayılan:
    # 3 tarama aralığı) "stale" sayılır ve karşılaştırmaya girmez.
    oi_staleness_factor: int = field(default_factory=lambda: int(os.getenv("OI_STALENESS_FACTOR", "3")))


CFG = Config()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT"}

# OI + funding bileşenlerinin skora katabileceği TEORİK MAKSİMUM.
# oi (18) + structure (12) + funding (8) = 38.
# Bu değer, bir adayın türev veri çekilmeden "zaten elenecek mi"
# yoksa "belki eşiği geçer mi" olduğunu anlamak için kullanılır.
MAX_DERIVATIVE_BONUS = 38


# ============================================================
# LOG
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("balina-v5")


def validate_config() -> None:
    if not TOKEN or not CHAT:
        log.warning(
            "⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik. "
            "Sinyaller hesaplanacak ama Telegram'a gönderilemeyecek."
        )

    if not (CFG.early <= CFG.strong <= CFG.whale):
        log.warning(
            "⚠️ Skor eşikleri beklenmedik sırada: EARLY=%d STRONG=%d WHALE=%d",
            CFG.early, CFG.strong, CFG.whale,
        )


# ============================================================
# HTTP (retry + backoff'lu ortak session)
# ============================================================

def build_session() -> requests.Session:
    s = requests.Session()
    r = Retry(
        total=3, connect=3, read=3, backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    a = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=r)
    s.mount("https://", a)
    s.mount("http://", a)
    s.headers.update({"User-Agent": "BalinaRadari-V5/1.1"})
    return s


S = build_session()


# ============================================================
# FLASK / RAILWAY  (orijinal ile aynı)
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V5 Aktif!"


@app.route("/health")
def health():
    return {"status": "ok", "bot": "Balina Radarı V5"}


# ============================================================
# BINANCE / TELEGRAM İSTEMCİLERİ
# ============================================================

def api(base: str, path: str, params: Optional[dict] = None) -> Any:
    r = S.get(base + path, params=params, timeout=CFG.timeout)
    r.raise_for_status()
    return r.json()


def telegram(text: str) -> bool:
    if not TOKEN or not CHAT:
        return False
    try:
        r = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT, "text": text},
            timeout=CFG.timeout,
        )
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except Exception as e:
        log.error("Telegram hatası: %s", e)
        return False


def tickers(base: str) -> List[dict]:
    try:
        return api(base, "/api/v3/ticker/24hr" if base == SPOT else "/fapi/v1/ticker/24hr")
    except Exception as e:
        log.error("Ticker hatası (%s): %s", base, e)
        return []


def klines(base: str, symbol: str, interval: str, limit: int = 70) -> List[list]:
    try:
        return api(
            base,
            "/api/v3/klines" if base == SPOT else "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
    except Exception as e:
        log.debug("%s %s kline hatası: %s", symbol, interval, e)
        return []


def open_interest(symbol: str) -> Optional[float]:
    try:
        return float(api(FUT, "/fapi/v1/openInterest", {"symbol": symbol})["openInterest"])
    except Exception as e:
        log.debug("%s OI hatası: %s", symbol, e)
        return None


def funding(symbol: str) -> Optional[float]:
    """Funding rate. Hata durumunda None döner (0.0 DEĞİL) —
    aksi halde bir API hatası yanlışlıkla 'funding dengeli' diye
    puana girer."""
    try:
        return float(api(FUT, "/fapi/v1/premiumIndex", {"symbol": symbol})["lastFundingRate"])
    except Exception as e:
        log.debug("%s funding hatası: %s", symbol, e)
        return None


# ============================================================
# TEKNİK YARDIMCILAR
# ============================================================

def pct(a: Optional[float], b: Optional[float]) -> float:
    if a is None or b is None or a <= 0:
        return 0.0
    return (b - a) / a * 100


def ratio(a: float, b: float) -> float:
    return a / b if b > 0 else 0.0


def clamp(x: float) -> int:
    return max(0, min(100, int(x)))


def rsi(c: List[float], n: int = 14) -> Optional[float]:
    if len(c) < n + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    for i in range(n, len(gains)):
        avg_gain = ((n - 1) * avg_gain + gains[i]) / n
        avg_loss = ((n - 1) * avg_loss + losses[i]) / n

    if avg_loss == 0:
        return 100.0

    return 100 - 100 / (1 + avg_gain / avg_loss)


# ============================================================
# KALICI STATE (cooldown + OI referansı)
# ============================================================

class DB:
    """Cooldown ve OI referans değerlerini kalıcı tutar.

    NOT: Railway'de persistent volume bağlı değilse, bu dosya
    her deploy/restart'ta sıfırlanır.
    """

    def __init__(self, path: str):
        self.path = path
        self.lock = Lock()
        with self.lock, sqlite3.connect(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY, sent REAL, score REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS oi(symbol TEXT PRIMARY KEY, value REAL, ts REAL)")

    def get_oi_reference(self, symbol: str) -> Optional[float]:
        """Stale değilse önceki OI değerini döner, aksi halde None."""
        with self.lock, sqlite3.connect(self.path) as c:
            row = c.execute("SELECT value, ts FROM oi WHERE symbol=?", (symbol,)).fetchone()

        if row is None:
            return None

        value, ts = row
        max_age = CFG.scan_interval * CFG.oi_staleness_factor

        if time.time() - ts > max_age:
            log.debug("%s OI referansı stale (%.0fs), yok sayılıyor", symbol, time.time() - ts)
            return None

        return value

    def put_oi(self, symbol: str, value: Optional[float]) -> None:
        if value is None:
            return
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute(
                """INSERT INTO oi VALUES(?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, ts=excluded.ts""",
                (symbol, value, time.time()),
            )

    def is_on_cooldown(self, symbol: str) -> bool:
        with self.lock, sqlite3.connect(self.path) as c:
            row = c.execute("SELECT sent FROM state WHERE symbol=?", (symbol,)).fetchone()
        return bool(row and time.time() - row[0] < CFG.cooldown)

    def mark_sent(self, symbol: str, score: float) -> None:
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute(
                """INSERT INTO state VALUES(?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent, score=excluded.score""",
                (symbol, time.time(), score),
            )


DBS = DB(CFG.db)


# ============================================================
# ADAY TOPLAMA
# ============================================================

def candidates(spot_tickers: List[dict], fut_tickers: List[dict]) -> List[str]:
    fut_map = {x.get("symbol"): x for x in fut_tickers}
    out = []

    for x in spot_tickers:
        symbol = x.get("symbol", "")

        if not symbol.endswith("USDT") or symbol in EXCLUDED:
            continue
        if any(symbol.endswith(z) for z in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
            continue

        fut = fut_map.get(symbol)
        if not fut:
            continue

        try:
            if float(x.get("quoteVolume", 0)) < CFG.min_volume:
                continue
            if float(fut.get("quoteVolume", 0)) < CFG.min_volume:
                continue
        except (TypeError, ValueError):
            continue

        out.append(symbol)

    return out


# ============================================================
# METRİK YAPILARI
# ============================================================

@dataclass
class BaseMetrics:
    price: float
    vr: float          # spot hacim oranı
    fvr: float         # futures hacim oranı
    va: float           # hacim ivmesi
    bp: float            # alıcı baskısı
    m5: float
    m15: float
    m30: float
    m60: float
    rsi: float
    ema_up: bool


@dataclass
class DerivativeMetrics:
    oi_change: float             # yüzde; veri yoksa 0.0
    oi_available: bool
    funding: Optional[float]     # None = veri alınamadı


def _extract_base_metrics(symbol: str) -> Tuple[Optional[BaseMetrics], Optional[Dict[str, Any]]]:
    sp5 = klines(SPOT, symbol, "5m")
    sp15 = klines(SPOT, symbol, "15m")
    fu5 = klines(FUT, symbol, "5m")

    if min(len(sp5), len(sp15), len(fu5)) < 60:
        return None, {"status": "insufficient"}

    a, b, f = sp5[:-1], sp15[:-1], fu5[:-1]

    close = [float(x[4]) for x in a]
    vol = [float(x[7]) for x in a]
    buy = [float(x[10]) for x in a]
    fut_vol = [float(x[7]) for x in f]
    close_15 = [float(x[4]) for x in b]

    price = float(sp5[-1][4])
    avg_vol = sum(vol[-25:-1]) / 24
    avg_fut_vol = sum(fut_vol[-25:-1]) / 24

    if avg_vol <= 0 or avg_fut_vol <= 0:
        return None, {"status": "insufficient"}

    vr = ratio(vol[-1], avg_vol)
    fvr = ratio(fut_vol[-1], avg_fut_vol)
    va = pct(sum(vol[-6:-3]) / 3, sum(vol[-3:]) / 3)
    bp = ratio(buy[-1], vol[-1]) * 100

    m5 = pct(close[-2], close[-1])
    m15 = pct(close[-4], close[-1])
    m30 = pct(close[-7], close[-1])
    m60 = pct(close[-13], close[-1])

    rsi_value = rsi(close_15)
    if rsi_value is None:
        return None, {"status": "insufficient"}

    ema20 = sum(close_15[-20:]) / 20
    ema50 = sum(close_15[-50:]) / 50

    metrics = BaseMetrics(
        price=price, vr=vr, fvr=fvr, va=va, bp=bp,
        m5=m5, m15=m15, m30=m30, m60=m60,
        rsi=rsi_value, ema_up=ema20 > ema50,
    )

    return metrics, None


def _check_early_exit(metrics: BaseMetrics) -> Optional[Dict[str, Any]]:
    if metrics.m30 >= 18 or metrics.m60 >= 30:
        return {"status": "late", "score": 0}
    return None


def _score_base(m: BaseMetrics) -> Tuple[int, List[str]]:
    """Türev veriye (OI/funding) ihtiyaç duymayan puan bileşenleri."""

    score = 0
    reasons: List[str] = []

    def add(n, text):
        nonlocal score
        score += n
        reasons.append(text)

    if m.vr >= 4:
        add(18, "🚀 Spot hacmi 4x+")
    elif m.vr >= 3:
        add(14, "🔥 Spot hacmi 3x+")
    elif m.vr >= 2:
        add(10, "📈 Spot hacmi 2x+")

    if m.fvr >= 3:
        add(12, "⚡ Futures hacmi 3x+")
    elif m.fvr >= 2:
        add(9, "⚡ Futures hacmi 2x+")
    elif m.fvr >= 1.5:
        add(5, "📊 Futures hacmi destekliyor")

    if m.va >= 100:
        add(10, "🚀 Hacim ivmesi çok güçlü")
    elif m.va >= 50:
        add(7, "🔥 Hacim ivmesi yükseliyor")
    elif m.va >= 25:
        add(4, "📈 Hacim ivmesi pozitif")

    if m.bp >= 68:
        add(16, "🐋 Çok güçlü alıcı baskısı")
    elif m.bp >= 63:
        add(12, "🟢 Güçlü alıcı baskısı")
    elif m.bp >= 58:
        add(8, "🟢 Alıcı baskısı pozitif")
    elif m.bp >= 54:
        add(4, "🟡 Alıcı baskısı yükseliyor")

    if 0.2 <= m.m5 <= 2.5:
        add(7, "🎯 5m erken momentum")
    elif 2.5 < m.m5 <= 4.5:
        add(4, "📈 5m momentum güçleniyor")
    elif m.m5 > 6:
        score -= 7
        reasons.append("⏰ 5m hareket fazla ilerledi")

    if 0 < m.m15 < 4:
        add(6, "🎯 15m erken hareket")
    elif 4 <= m.m15 < 7:
        add(3, "📈 15m momentum güçleniyor")
    elif m.m15 >= 10:
        score -= 8
        reasons.append("⏰ 15m hareket ilerledi")

    if 0 < m.m30 < 7:
        add(4, "📈 30m kontrollü hareket")
    elif m.m30 >= 12:
        score -= 8
        reasons.append("⏰ 30m hareket fazla ilerledi")

    if 42 <= m.rsi <= 62:
        add(7, "📊 RSI erken bölge")
    elif 62 < m.rsi <= 70:
        add(3, "📊 RSI güçleniyor")
    elif m.rsi > 78:
        score -= 10
        reasons.append("⚠️ RSI aşırı yüksek")

    if m.ema_up:
        add(6, "📈 EMA trendi yukarı")

    if m.vr >= 2 and m.bp >= 58 and abs(m.m15) < 5:
        add(6, "🔮 Erken birikim rejimi")

    return score, reasons


def _fetch_derivatives(symbol: str) -> DerivativeMetrics:
    oi_now = open_interest(symbol)
    oi_ref = DBS.get_oi_reference(symbol)

    oi_change = pct(oi_ref, oi_now) if (oi_ref is not None and oi_now is not None) else 0.0
    oi_available = oi_ref is not None and oi_now is not None

    DBS.put_oi(symbol, oi_now)

    return DerivativeMetrics(
        oi_change=oi_change,
        oi_available=oi_available,
        funding=funding(symbol),
    )


def _score_derivatives(m: BaseMetrics, d: DerivativeMetrics) -> Tuple[int, List[str], str]:
    score = 0
    reasons: List[str] = []
    structure = "NEUTRAL"

    def add(n, text):
        nonlocal score
        score += n
        reasons.append(text)

    oc = d.oi_change

    if d.oi_available:
        if oc >= 8:
            add(18, f"🐋 OI güçlü artıyor (+%{oc:.1f})")
        elif oc >= 5:
            add(13, f"📈 OI artıyor (+%{oc:.1f})")
        elif oc >= 3:
            add(8, f"📊 OI yükseliyor (+%{oc:.1f})")

        if abs(m.m15) <= 1.5 and oc >= 3:
            add(12, "🔮 Fiyat sakin / OI yükseliyor")
            structure = "PRICE_FLAT_OI_UP"
        elif m.m15 > 0 and oc > 0:
            add(7, "📈 Fiyat + OI birlikte yükseliyor")
            structure = "PRICE_UP_OI_UP"
        elif m.m15 < 0 and oc > 0:
            add(2, "⚠️ Fiyat düşerken OI yükseliyor")
            structure = "PRICE_DOWN_OI_UP"
        elif m.m15 > 0 and oc < 0:
            structure = "PRICE_UP_OI_DOWN"
    else:
        reasons.append("ℹ️ OI verisi alınamadı, bu bileşen atlandı")

    if d.funding is None:
        reasons.append("ℹ️ Funding verisi alınamadı, bu bileşen atlandı")
    elif d.funding < -0.0005:
        add(8, "⚡ Negatif funding / squeeze potansiyeli")
    elif d.funding <= 0.0005:
        add(5, "⚖️ Funding dengeli")
    elif d.funding > 0.001:
        score -= 4
        reasons.append("⚠️ Funding fazla pozitif")

    return score, reasons, structure


def _classify(score: int) -> Optional[str]:
    if score >= CFG.whale:
        return "🚨 ÇOK GÜÇLÜ ERKEN HAREKET"
    if score >= CFG.strong:
        return "🟢 GÜÇLENEN ERKEN SİNYAL"
    if score >= CFG.early:
        return "🟡 ERKEN HAREKET UYARISI"
    return None


def analyze(symbol: str) -> Dict[str, Any]:
    """Tam analiz hattı:
    temel metrik -> erken çıkış -> temel skor ->
    (yalnızca gerekliyse) türev veri -> türev skor -> sınıflandırma
    """
    try:
        metrics, error = _extract_base_metrics(symbol)
        if error is not None:
            return error

        early_exit = _check_early_exit(metrics)
        if early_exit is not None:
            return early_exit

        base_score, base_reasons = _score_base(metrics)

        # Türev veri çekmenin anlamı yoksa (matematiksel olarak
        # eşiği geçemez), OI/funding isteklerini hiç atma.
        if base_score + MAX_DERIVATIVE_BONUS < CFG.early:
            return {"status": "below_score", "score": clamp(base_score)}

        deriv = _fetch_derivatives(symbol)
        deriv_score, deriv_reasons, structure = _score_derivatives(metrics, deriv)

        total_score = clamp(base_score + deriv_score)
        signal_type = _classify(total_score)

        if signal_type is None:
            return {"status": "below_score", "score": total_score}

        return {
            "status": "signal",
            "symbol": symbol,
            "type": signal_type,
            "score": total_score,
            "price": metrics.price,
            "vr": metrics.vr, "fvr": metrics.fvr, "va": metrics.va, "bp": metrics.bp,
            "m5": metrics.m5, "m15": metrics.m15, "m30": metrics.m30, "m60": metrics.m60,
            "rsi": metrics.rsi, "ema": metrics.ema_up,
            "oi": deriv.oi_change, "fund": deriv.funding if deriv.funding is not None else 0.0,
            "structure": structure,
            "reasons": base_reasons + deriv_reasons,
        }

    except Exception as e:
        log.error("%s analiz hatası: %s", symbol, e)
        return {"status": "error"}


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def msg(r: Dict[str, Any]) -> str:
    rs = "\n".join("• " + x for x in r["reasons"])
    return (
        f"🐋 BALİNA RADARI V5\n━━━━━━━━━━━━━━━━━━\n\n{r['type']}\n\n"
        f"🪙 #{r['symbol']}\n💰 Fiyat: {r['price']:.8g}\n🎯 SCORE: {r['score']}/10
