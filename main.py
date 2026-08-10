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
# 🐋 BALİNA RADARI V6 — PRECISION EARLY-MOVE ENGINE
# (PROFESYONEL / DÜZELTİLMİŞ SÜRÜM)
#
# Orijinal V6'ya göre DÜZELTİLENLER:
#   - KRİTİK BUG: analyze(), collect_candidates()'te hesaplanan
#     gerçek change_24h değerini kullanmıyordu; sahte bir
#     Candidate(change_24h=0.0) ile eziyordu. Bu yüzden
#     "Overextended / Tepe Filtresi" hiç tetiklenmiyordu.
#     -> Artık gerçek Candidate objesi analyze()'a taşınıyor.
#   - urllib3 Retry parametre uyumluluğu (allowed_methods /
#     method_whitelist) otomatik algılanıyor.
#   - base_gate artık early - derivative_max_bonus'tan otomatik
#     türetiliyor (env var ile override edilebilir), böylece
#     ikisi birbirinden bağımsız değiştirilip tutarsız hale
#     gelemiyor.
#   - Worker sonuçları try/except ile korunuyor.
#   - Kullanılmayan momentum_1m alanı kaldırıldı.
#
# Skorlama ağırlıkları DEĞİŞMEDİ.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

@dataclass(frozen=True)
class Config:
    min_volume: float = field(default_factory=lambda: float(os.getenv("MIN_VOLUME_USDT", "750000")))
    scan_interval: int = field(default_factory=lambda: int(os.getenv("SCAN_INTERVAL", "300")))
    workers: int = field(default_factory=lambda: int(os.getenv("MAX_WORKERS", "8")))
    early: int = field(default_factory=lambda: int(os.getenv("EARLY_SCORE", "68")))
    strong: int = field(default_factory=lambda: int(os.getenv("STRONG_SCORE", "80")))
    whale: int = field(default_factory=lambda: int(os.getenv("WHALE_SCORE", "90")))
    max_signals: int = field(default_factory=lambda: int(os.getenv("MAX_SIGNALS_PER_SCAN", "5")))
    cooldown: int = field(default_factory=lambda: int(os.getenv("SIGNAL_COOLDOWN", "7200")))
    timeout: int = field(default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT", "8")))
    db: str = field(default_factory=lambda: os.getenv("STATE_DB_PATH", "balina_v6.db"))
    oi_staleness_factor: int = field(default_factory=lambda: int(os.getenv("OI_STALENESS_FACTOR", "3")))

    derivative_max_bonus: int = field(default_factory=lambda: int(os.getenv("DERIVATIVE_MAX_BONUS", "25")))
    overextended_score_cap: int = field(default_factory=lambda: int(os.getenv("OVEREXTENDED_SCORE_CAP", "67")))
    extension_24h_pct: float = field(default_factory=lambda: float(os.getenv("EXTENSION_24H_PCT", "18")))
    local_high_position: float = field(default_factory=lambda: float(os.getenv("LOCAL_HIGH_POSITION", "0.88")))

    # base_gate: derivatives'ı çekmenin anlamlı olduğu minimum
    # temel skor. Belirtilmezse otomatik olarak
    # (early - derivative_max_bonus) olarak hesaplanır, yani
    # "türev bonusu maksimum olsa bile eşiği geçemeyecek
    # adaylar için OI/funding isteği hiç atılmaz" mantığı korunur.
    base_gate: Optional[int] = field(default_factory=lambda: (
        int(os.getenv("BASE_GATE")) if os.getenv("BASE_GATE") else None
    ))

    def effective_base_gate(self) -> int:
        if self.base_gate is not None:
            return self.base_gate
        return max(0, self.early - self.derivative_max_bonus)


CFG = Config()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

SPOT = "https://api.binance.com"
FUT = "https://fapi.binance.com"

EXCLUDED = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT"}
LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


# ============================================================
# LOG & SESSION
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("balina-v6")


def validate_config() -> None:
    if not TOKEN or not CHAT:
        log.warning("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik.")

    if not (CFG.early <= CFG.strong <= CFG.whale):
        log.warning(
            "⚠️ Skor eşikleri beklenmedik sırada: EARLY=%d STRONG=%d WHALE=%d",
            CFG.early, CFG.strong, CFG.whale,
        )

    if CFG.overextended_score_cap < CFG.early:
        log.warning(
            "⚠️ OVEREXTENDED_SCORE_CAP (%d) EARLY_SCORE'dan (%d) düşük — "
            "'aşırı uzamış' işaretlenen adaylar hiçbir zaman sinyal üretemeyecek "
            "(tam bloklama gibi davranır). Bu kasıtlıysa sorun yok.",
            CFG.overextended_score_cap, CFG.early,
        )

    log.info(
        "🧮 effective_base_gate=%d (early=%d - derivative_max_bonus=%d)",
        CFG.effective_base_gate(), CFG.early, CFG.derivative_max_bonus,
    )


def build_session() -> requests.Session:
    """urllib3 sürümüne göre Retry parametre adı değişir
    (allowed_methods >= 1.26, method_whitelist < 1.26). İkisini
    de deneyip kurulu sürüme uyanı kullanıyoruz; bu import
    anında TypeError ile çökmeyi engeller."""

    retry_kwargs = dict(
        total=3, connect=3, read=3, backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )

    try:
        retry_strategy = Retry(allowed_methods=["GET", "POST"], **retry_kwargs)
    except TypeError:
        retry_strategy = Retry(method_whitelist=["GET", "POST"], **retry_kwargs)

    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=retry_strategy)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "BalinaRadari-V6/1.1"})
    return s


S = build_session()


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V6 Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V6",
        "engine": "precision-early-move",
        "scan_interval": CFG.scan_interval,
        "effective_base_gate": CFG.effective_base_gate(),
    }


# ============================================================
# API
# ============================================================

def api(base: str, path: str, params: Optional[dict] = None) -> Any:
    response = S.get(base + path, params=params, timeout=CFG.timeout)
    response.raise_for_status()
    return response.json()


def telegram(text: str) -> bool:
    if not TOKEN or not CHAT:
        log.error("Telegram credential eksik.")
        return False
    try:
        response = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT, "text": text},
            timeout=CFG.timeout,
        )
        response.raise_for_status()
        return bool(response.json().get("ok"))
    except Exception as error:
        log.error("Telegram hatası: %s", error)
        return False


def tickers(base: str) -> List[dict]:
    try:
        path = "/api/v3/ticker/24hr" if base == SPOT else "/fapi/v1/ticker/24hr"
        result = api(base, path)
        return result if isinstance(result, list) else []
    except Exception as error:
        log.error("Ticker hatası (%s): %s", base, error)
        return []


def klines(base: str, symbol: str, interval: str, limit: int = 80) -> List[list]:
    try:
        path = "/api/v3/klines" if base == SPOT else "/fapi/v1/klines"
        result = api(base, path, {"symbol": symbol, "interval": interval, "limit": limit})
        return result if isinstance(result, list) else []
    except Exception as error:
        log.debug("%s %s %s kline hatası: %s", symbol, interval, base, error)
        return []


def open_interest(symbol: str) -> Optional[float]:
    try:
        result = api(FUT, "/fapi/v1/openInterest", {"symbol": symbol})
        return float(result["openInterest"])
    except Exception as error:
        log.debug("%s OI hatası: %s", symbol, error)
        return None


def funding(symbol: str) -> Optional[float]:
    try:
        result = api(FUT, "/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(result["lastFundingRate"])
    except Exception as error:
        log.debug("%s funding hatası: %s", symbol, error)
        return None


# ============================================================
# MATH
# ============================================================

def pct(old: Optional[float], new: Optional[float]) -> float:
    if old is None or new is None or old <= 0:
        return 0.0
    return ((new - old) / old) * 100


def ratio(a: float, b: float) -> float:
    return a / b if b > 0 else 0.0


def clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = ((value - result) * multiplier) + result
    return result


# ============================================================
# DATABASE
# ============================================================

class DB:
    def __init__(self, path: str):
        self.path = path
        self.lock = Lock()
        with self.lock, sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS state (symbol TEXT PRIMARY KEY, sent REAL NOT NULL, score REAL)")
            connection.execute("CREATE TABLE IF NOT EXISTS oi (symbol TEXT PRIMARY KEY, value REAL NOT NULL, ts REAL NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, score REAL, ts REAL)")

    def get_oi_reference(self, symbol: str) -> Optional[float]:
        with self.lock, sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT value, ts FROM oi WHERE symbol=?", (symbol,)).fetchone()
        if row is None:
            return None
        value, timestamp = row
        max_age = CFG.scan_interval * CFG.oi_staleness_factor
        if (time.time() - timestamp) > max_age:
            return None
        return float(value)

    def put_oi(self, symbol: str, value: Optional[float]) -> None:
        if value is None:
            return
        with self.lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """INSERT INTO oi (symbol, value, ts) VALUES (?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET value=excluded.value, ts=excluded.ts""",
                (symbol, value, time.time()),
            )

    def is_on_cooldown(self, symbol: str) -> bool:
        with self.lock, sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT sent FROM state WHERE symbol=?", (symbol,)).fetchone()
        if row is None:
            return False
        return (time.time() - row[0]) < CFG.cooldown

    def mark_sent(self, symbol: str, score: float) -> None:
        with self.lock, sqlite3.connect(self.path) as connection:
            connection.execute(
                """INSERT INTO state (symbol, sent, score) VALUES (?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET sent=excluded.sent, score=excluded.score""",
                (symbol, time.time(), score),
            )


DBS = DB(CFG.db)


# ============================================================
# CANDIDATES & METRICS
# ============================================================

@dataclass
class Candidate:
    symbol: str
    change_24h: float
    spot_volume: float
    futures_volume: float


def collect_candidates(spot_tickers: List[dict], futures_tickers: List[dict]) -> List[Candidate]:
    futures_map = {x.get("symbol"): x for x in futures_tickers}
    result = []
    for spot in spot_tickers:
        symbol = spot.get("symbol", "")
        if not symbol.endswith("USDT") or symbol in EXCLUDED:
            continue
        if any(symbol.endswith(x) for x in LEVERAGED_SUFFIXES):
            continue
        futures = futures_map.get(symbol)
        if not futures:
            continue
        try:
            spot_volume = float(spot.get("quoteVolume", 0))
            futures_volume = float(futures.get("quoteVolume", 0))
            change_24h = float(spot.get("priceChangePercent", 0))
        except (TypeError, ValueError):
            continue

        if spot_volume < CFG.min_volume or futures_volume < CFG.min_volume:
            continue

        result.append(Candidate(symbol=symbol, change_24h=change_24h, spot_volume=spot_volume, futures_volume=futures_volume))
    return result


@dataclass
class BaseMetrics:
    price: float
    volume_ratio: float
    futures_volume_ratio: float
    volume_acceleration: float
    buy_pressure: float
    momentum_5m: float
    momentum_15m: float
    momentum_30m: float
    momentum_60m: float
    live_volume_ratio: float
    live_buy_pressure: float
    live_momentum: float
    rsi: float
    ema20: float
    ema50: float
    ema_up: bool
    local_position: float
    local_high: float
    local_low: float
    volume_quality: float
    change_24h: float


@dataclass
class DerivativeMetrics:
    oi_change: float
    oi_available: bool
    funding: Optional[float]


def extract_base_metrics(candidate: Candidate) -> Tuple[Optional[BaseMetrics], Optional[Dict[str, Any]]]:
    symbol = candidate.symbol
    spot_5m = klines(SPOT, symbol, "5m", 80)
    spot_15m = klines(SPOT, symbol, "15m", 80)
    futures_5m = klines(FUT, symbol, "5m", 80)

    if len(spot_5m) < 60 or len(spot_15m) < 60 or len(futures_5m) < 60:
        return None, {"status": "insufficient"}

    closed_5m = spot_5m[:-1]
    live_5m = spot_5m[-1]
    closed_15m = spot_15m[:-1]
    closed_futures_5m = futures_5m[:-1]

    closes_5m = [float(x[4]) for x in closed_5m]
    volumes_5m = [float(x[7]) for x in closed_5m]
    taker_buy_5m = [float(x[10]) for x in closed_5m]
    closes_15m = [float(x[4]) for x in closed_15m]
    futures_volumes = [float(x[7]) for x in closed_futures_5m]

    if len(closes_5m) < 20:
        return None, {"status": "insufficient"}

    closed_price = closes_5m[-1]
    live_price = float(live_5m[4])
    price = live_price if live_price > 0 else closed_price

    historical_volumes = volumes_5m[-25:-1]
    historical_futures = futures_volumes[-25:-1]

    if len(historical_volumes) < 10:
        return None, {"status": "insufficient"}

    avg_volume = sum(historical_volumes) / len(historical_volumes)
    avg_futures_volume = sum(historical_futures) / len(historical_futures)

    if avg_volume <= 0 or avg_futures_volume <= 0:
        return None, {"status": "insufficient"}

    current_volume = volumes_5m[-1]
    futures_current_volume = futures_volumes[-1]

    volume_ratio = ratio(current_volume, avg_volume)
    futures_volume_ratio = ratio(futures_current_volume, avg_futures_volume)

    recent_vol_avg = sum(volumes_5m[-4:-1]) / 3
    earlier_vol_avg = sum(volumes_5m[-7:-4]) / 3
    volume_acceleration = pct(earlier_vol_avg, recent_vol_avg)

    last_buy = taker_buy_5m[-1]
    buy_pressure = ratio(last_buy, current_volume) * 100

    momentum_5m = pct(closes_5m[-2], closes_5m[-1])
    momentum_15m = pct(closes_5m[-4], closes_5m[-1])
    momentum_30m = pct(closes_5m[-7], closes_5m[-1])
    momentum_60m = pct(closes_5m[-13], closes_5m[-1])

    live_open = float(live_5m[1])
    live_close = price
    live_vol = float(live_5m[7])
    live_buy = float(live_5m[10])

    live_momentum = pct(live_open, live_close)
    live_buy_pressure = ratio(live_buy, live_vol) * 100 if live_vol > 0 else buy_pressure
    live_volume_ratio = ratio(live_vol, avg_volume)

    rsi_value = calculate_rsi(closes_15m)
    if rsi_value is None:
        return None, {"status": "insufficient"}

    ema20_val = ema(closes_15m, 20)
    ema50_val = ema(closes_15m, 50)
    if ema20_val is None or ema50_val is None:
        return None, {"status": "insufficient"}

    ema_up = ema20_val > ema50_val

    recent_closes = closes_5m[-20:]
    local_high = max(recent_closes)
    local_low = min(recent_closes)
    local_range = local_high - local_low
    local_position = (price - local_low) / local_range if local_range > 0 else 0.5

    vol_slice = volumes_5m[-5:]
    volume_quality = sum(vol_slice) / (len(vol_slice) * avg_volume) if avg_volume > 0 else 1.0

    metrics = BaseMetrics(
        price=price,
        volume_ratio=volume_ratio,
        futures_volume_ratio=futures_volume_ratio,
        volume_acceleration=volume_acceleration,
        buy_pressure=buy_pressure,
        momentum_5m=momentum_5m,
        momentum_15m=momentum_15m,
        momentum_30m=momentum_30m,
        momentum_60m=momentum_60m,
        live_volume_ratio=live_volume_ratio,
        live_buy_pressure=live_buy_pressure,
        live_momentum=live_momentum,
        rsi=rsi_value,
        ema20=ema20_val,
        ema50=ema50_val,
        ema_up=ema_up,
        local_position=local_position,
        local_high=local_high,
        local_low=local_low,
        volume_quality=volume_quality,
        change_24h=candidate.change_24h,
    )
    return metrics, None


# ============================================================
# SCORING & ANALYSIS
# ============================================================

def _score_base(m: BaseMetrics) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    def add(n, text):
        nonlocal score
        score += n
        reasons.append(text)

    if m.volume_ratio >= 4: add(16, "🚀 Spot hacmi 4x+")
    elif m.volume_ratio >= 3: add(12, "🔥 Spot hacmi 3x+")
    elif m.volume_ratio >= 2: add(8, "📈 Spot hacmi 2x+")

    if m.futures_volume_ratio >= 3: add(10, "⚡ Futures hacmi 3x+")
    elif m.futures_volume_ratio >= 2: add(7, "⚡ Futures hacmi 2x+")
    elif m.futures_volume_ratio >= 1.5: add(4, "📊 Futures hacmi destekliyor")

    if m.volume_acceleration >= 100: add(8, "🚀 Hacim ivmesi çok güçlü")
    elif m.volume_acceleration >= 50: add(5, "🔥 Hacim ivmesi yükseliyor")

    if m.buy_pressure >= 68: add(14, "🐋 Çok güçlü alıcı baskısı")
    elif m.buy_pressure >= 63: add(10, "🟢 Güçlü alıcı baskısı")
    elif m.buy_pressure >= 58: add(6, "🟢 Alıcı baskısı pozitif")

    if 0.2 <= m.momentum_5m <= 2.5: add(6, "🎯 5m erken momentum")
    elif m.momentum_5m > 6: score -= 6; reasons.append("⏰ 5m hareket fazla ilerledi")

    if 0 < m.momentum_15m < 4: add(5, "🎯 15m erken hareket")
    elif m.momentum_15m >= 10: score -= 6; reasons.append("⏰ 15m hareket ilerledi")

    if 42 <= m.rsi <= 62: add(6, "📊 RSI erken bölge")
    elif m.rsi > 78: score -= 8; reasons.append("⚠️ RSI aşırı yüksek")

    if m.ema_up: add(5, "📈 EMA trendi yukarı")

    return score, reasons


def _fetch_derivatives(symbol: str) -> DerivativeMetrics:
    oi_now = open_interest(symbol)
    oi_ref = DBS.get_oi_reference(symbol)
    oi_change = pct(oi_ref, oi_now) i
