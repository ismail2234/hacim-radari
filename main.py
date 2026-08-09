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
import pandas as pd
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V4 (PROFESYONEL YAPI)
# Erken hareket + hacim ivmesi + alıcı baskısı + momentum
#
# Bu versiyon V4'ün skorlama mantığını DEĞİŞTİRMEDEN şu
# iyileştirmeleri ekler:
#   - Config: tüm eşikler env var ile ayarlanabilir
#   - Binance/Telegram istekleri için retry + backoff
#   - Cooldown durumu SQLite'ta kalıcı (restart'ta kaybolmaz)
#   - analyze_coin() küçük, test edilebilir alt fonksiyonlara
#     bölündü (metrik hesabı / erken çıkış / skorlama / sınıf)
#
# ⚠️ Dosyanın en altındaki Railway/Gunicorn başlatma bloğu
# (scanner_thread + __main__) bilinçli olarak DEĞİŞTİRİLMEDİ.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

@dataclass(frozen=True)
class Config:
    min_volume_try: float = field(
        default_factory=lambda: float(os.getenv("MIN_VOLUME_TRY", "100000"))
    )
    min_volume_usdt: float = field(
        default_factory=lambda: float(os.getenv("MIN_VOLUME_USDT", "500000"))
    )

    scan_interval: int = field(
        default_factory=lambda: int(os.getenv("SCAN_INTERVAL", "300"))
    )
    signal_cooldown: int = field(
        default_factory=lambda: int(os.getenv("SIGNAL_COOLDOWN", "3600"))
    )

    max_workers: int = field(
        default_factory=lambda: int(os.getenv("MAX_WORKERS", "10"))
    )

    # Kademeli sinyal eşikleri (V4 ile aynı varsayılanlar)
    early_score: int = field(
        default_factory=lambda: int(os.getenv("EARLY_SCORE", "60"))
    )
    strong_score: int = field(
        default_factory=lambda: int(os.getenv("STRONG_SCORE", "75"))
    )
    whale_score: int = field(
        default_factory=lambda: int(os.getenv("WHALE_SCORE", "88"))
    )

    max_signals_per_scan: int = field(
        default_factory=lambda: int(os.getenv("MAX_SIGNALS_PER_SCAN", "5"))
    )

    # Kalıcı state için SQLite dosya yolu.
    # NOT: Railway'de "persistent volume" bağlı değilse, her
    # deploy/restart'ta bu dosya da sıfırlanır. Kalıcılık
    # istiyorsan Railway Volumes veya harici bir DB (Postgres/
    # Redis) kullanman gerekir.
    state_db_path: str = field(
        default_factory=lambda: os.getenv("STATE_DB_PATH", "balina_state.db")
    )

    request_timeout: int = field(
        default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT", "10"))
    )


CONFIG = Config()

# Telegram credential'ları - orijinal koddaki gibi düz env var okuması.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

EXCLUDED_SUFFIXES = (
    "UPUSDT",
    "DOWNUSDT",
    "BULLUSDT",
    "BEARUSDT",
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "USDPUSDT",
    "DAIUSDT",
)


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("balina-radari-v4")


def validate_config() -> None:
    """Başlangıçta kritik config eksiklerini erkenden logla."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(
            "⚠️ TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID tanımlı değil. "
            "Sinyaller hesaplanacak ama Telegram'a gönderilemeyecek."
        )

    if CONFIG.early_score >= CONFIG.strong_score >= CONFIG.whale_score:
        logger.warning(
            "⚠️ Skor eşikleri beklenmedik sırada: "
            "EARLY=%d STRONG=%d WHALE=%d",
            CONFIG.early_score,
            CONFIG.strong_score,
            CONFIG.whale_score,
        )


# ============================================================
# HTTP (retry + backoff'lu ortak session)
# ============================================================

def build_session() -> requests.Session:
    session = requests.Session()

    retry_strategy = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        pool_connections=30,
        pool_maxsize=30,
        max_retries=retry_strategy,
    )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({"User-Agent": "BalinaRadari/4.1"})

    return session


session = build_session()


# ============================================================
# FLASK / RAILWAY  (orijinal ile aynı)
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V4 Aktif ve Çalışıyor!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V4"
    }


def run_flask():
    port = int(os.getenv("PORT", "8080"))

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error(
            "TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik!"
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = session.post(
            url,
            json=data,
            timeout=10
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            logger.error(
                "Telegram API hatası: %s",
                result
            )
            return False

        return True

    except Exception as error:
        logger.error(
            "Telegram bağlantı hatası: %s",
            error
        )
        return False


# ============================================================
# BINANCE
# ============================================================

def get_tickers() -> List[dict]:
    try:
        response = session.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            timeout=CONFIG.request_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    except Exception as error:
        logger.error("Binance ticker hatası: %s", error)
        return []


def get_klines(symbol: str, interval: str, limit: int = 100) -> List[list]:
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    try:
        response = session.get(
            "https://api.binance.com/api/v3/klines",
            params=params,
            timeout=CONFIG.request_timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    except Exception as error:
        logger.error("%s %s veri hatası: %s", symbol, interval, error)
        return []


# ============================================================
# TEKNİK HESAPLAMALAR
# ============================================================

def percent_change(old: Optional[float], new: Optional[float]) -> float:
    if old is None or new is None or old <= 0:
        return 0.0
    return ((new - old) / old) * 100


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None

    series = pd.Series(closes, dtype=float)
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    last_loss = average_loss.iloc[-1]

    if pd.isna(last_loss):
        return None

    if last_loss == 0:
        return 100.0

    rs = average_gain.iloc[-1] / last_loss
    return float(100 - (100 / (1 + rs)))


def format_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}"
    if price >= 0.01:
        return f"{price:,.6f}"
    return f"{price:,.8f}"


# ============================================================
# KALICI STATE (cooldown) - SQLite
# ============================================================

class SignalStore:
    """Gönderilen sinyallerin cooldown zamanını kalıcı olarak tutar.

    Amaç: servis yeniden başladığında (deploy/crash/restart) aynı
    coin için kısa süre içinde tekrar sinyal atılmasını önlemek.

    NOT: Bu dosya konteynerin diskinde tutulur. Railway'de kalıcı
    bir volume bağlı değilse, deploy sırasında yine sıfırlanır.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=10)

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sent_signals (
                    symbol TEXT PRIMARY KEY,
                    sent_at REAL NOT NULL
                )
                """
            )

    def is_on_cooldown(self, symbol: str, cooldown_seconds: int) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT sent_at FROM sent_signals WHERE symbol = ?",
                (symbol,),
            ).fetchone()

        if row is None:
            return False

        return (time.time() - row[0]) < cooldown_seconds

    def mark_sent(self, symbol: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sent_signals (symbol, sent_at) VALUES (?, ?)
                ON CONFLICT(symbol) DO UPDATE SET sent_at = excluded.sent_at
                """,
                (symbol, time.time()),
            )


signal_store = SignalStore(CONFIG.state_db_path)


# ============================================================
# METRİK YAPISI
# ============================================================

@dataclass
class CoinMetrics:
    price: float
    rsi: float
    trend_up: bool

    volume_ratio: float
    volume_change: float
    volume_acceleration: float
    live_volume_ratio: float

    buy_pressure: float
    live_buy_pressure: float
    average_pressure: float

    momentum_5m: float
    live_momentum_5m: float
    momentum_15m: float
    momentum_30m: float
    momentum_60m: float

    volume_15m_ratio: float


def _extract_metrics(symbol: str) -> Tuple[Optional[CoinMetrics], Optional[Dict[str, Any]]]:
    """Ham kline verisinden CoinMetrics üretir.

    Dönüş: (metrics, None) başarılıysa; (None, {"status":..., "reason":...})
    veri yetersizse.
    """

    candles_5m = get_klines(symbol, "5m", 100)
    candles_15m = get_klines(symbol, "15m", 100)

    if len(candles_5m) < 35:
        return None, {"status": "insufficient", "reason": "5m veri yetersiz"}

    if len(candles_15m) < 60:
        return None, {"status": "insufficient", "reason": "15m veri yetersiz"}

    # ---- 5m kapanmış mumlar ----
    closed_5m = candles_5m[:-1]
    closes_5m = [float(c[4]) for c in closed_5m]
    volumes_5m = [float(c[6]) for c in closed_5m]
    taker_buys_5m = [float(c[9]) for c in closed_5m]

    # ---- Açık 5m mum ----
    live_5m = candles_5m[-1]
    live_price = float(live_5m[4])
    live_quote_volume = float(live_5m[7])
    live_taker_buy_quote = float(live_5m[10])

    # ---- 15m kapanmış mumlar ----
    closed_15m = candles_15m[:-1]
    closes_15m = [float(c[4]) for c in closed_15m]
    volumes_15m = [float(c[6]) for c in closed_15m]

    closed_price = closes_5m[-1]
    price = live_price if live_price > 0 else closed_price

    # ---- Momentum ----
    momentum_5m = percent_change(closes_5m[-2], closed_price)
    momentum_15m = percent_change(closes_5m[-4], closed_price)
    momentum_30m = percent_change(closes_5m[-7], closed_price)
    momentum_60m = percent_change(closes_5m[-13], closed_price)
    live_momentum_5m = percent_change(closes_5m[-1], price)

    # ---- 5m hacim ----
    current_volume = volumes_5m[-1]
    old_volumes = volumes_5m[-25:-1]

    if len(old_volumes) < 10:
        return None, {"status": "insufficient", "reason": "hacim geçmişi yetersiz"}

    average_volume = sum(old_volumes) / len(old_volumes)

    if average_volume <= 0:
        return None, {"status": "insufficient", "reason": "ortalama hacim sıfır"}

    volume_ratio = current_volume / average_volume
    volume_change = percent_change(volumes_5m[-2], current_volume)

    # ---- Hacim ivmesi ----
    recent_volume = sum(volumes_5m[-3:]) / 3
    previous_volume = sum(volumes_5m[-6:-3]) / 3

    if previous_volume > 0:
        volume_acceleration = ((recent_volume - previous_volume) / previous_volume) * 100
    else:
        volume_acceleration = 0.0

    # ---- Açık mum hacim/baskı ----
    live_volume_ratio = live_quote_volume / average_volume if average_volume > 0 else 0
    live_buy_pressure = (
        (live_taker_buy_quote / live_quote_volume) * 100
        if live_quote_volume > 0
        else 0
    )

    # ---- Alıcı baskısı ----
    if current_volume <= 0:
        return None, {"status": "insufficient", "reason": "güncel hacim sıfır"}

    buy_pressure = (taker_buys_5m[-1] / current_volume) * 100

    pressures = []
    for index in range(-3, 0):
        volume = volumes_5m[index]
        if volume > 0:
            pressures.append((taker_buys_5m[index] / volume) * 100)

    average_pressure = sum(pressures) / len(pressures) if pressures else 0

    # ---- RSI / EMA ----
    rsi = calculate_rsi(closes_15m)
    if rsi is None:
        return None, {"status": "insufficient", "reason": "RSI hesaplanamadı"}

    series_15m = pd.Series(closes_15m, dtype=float)
    ema20 = series_15m.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = series_15m.ewm(span=50, adjust=False).mean().iloc[-1]
    trend_up = ema20 > ema50

    # ---- 15m hacim ----
    current_15m_volume = volumes_15m[-1]
    old_15m_volumes = volumes_15m[-21:-1]

    if not old_15m_volumes:
        return None, {"status": "insufficient", "reason": "15m hacim geçmişi yetersiz"}

    average_15m_volume = sum(old_15m_volumes) / len(old_15m_volumes)
    volume_15m_ratio = (
        current_15m_volume / average_15m_volume if average_15m_volume > 0 else 0
    )

    metrics = CoinMetrics(
        price=price,
        rsi=rsi,
        trend_up=trend_up,
        volume_ratio=volume_ratio,
        volume_change=volume_change,
        volume_acceleration=volume_acceleration,
        live_volume_ratio=live_volume_ratio,
        buy_pressure=buy_pressure,
        live_buy_pressure=live_buy_pressure,
        average_pressure=average_pressure,
        momentum_5m=momentum_5m,
        live_momentum_5m=live_momentum_5m,
        momentum_15m=momentum_15m,
        momentum_30m=momentum_30m,
        momentum_60m=momentum_60m,
        volume_15m_ratio=volume_15m_ratio,
    )

    return metrics, None


def _check_early_exit(metrics: CoinMetrics) -> Optional[Dict[str, Any]]:
    """Geç kalmış veya zayıf hareketleri skorlamaya girmeden eler."""

    if metrics.momentum_30m >= 18:
        return {"status": "late", "reason": "30 dk hareket fazla ilerlemiş"}

    if metrics.momentum_60m >= 30:
        return {"status": "late", "reason": "60 dk hareket fazla ilerlemiş"}

    if (
        metrics.momentum_15m < -3
        and metrics.buy_pressure < 55
        and metrics.volume_ratio < 1.5
    ):
        return {"status": "weak", "reason": "momentum ve alıcı baskısı zayıf"}

    return None


def _score_signal(metrics: CoinMetrics) -> Tuple[int, List[str]]:
    """V4'ün orijinal puanlama kurallarının birebir aynısı."""

    score = 0
    reasons: List[str] = []

    # HACİM
    if metrics.volume_ratio >= 4:
        score += 22
        reasons.append("🚀 Hacim 4x+")
    elif metrics.volume_ratio >= 3:
        score += 18
        reasons.append("🔥 Hacim 3x+")
    elif metrics.volume_ratio >= 2:
        score += 14
        reasons.append("📈 Hacim 2x+")
    elif metrics.volume_ratio >= 1.5:
        score += 8
        reasons.append("📊 Hacim normalin üzerinde")

    # HACİM DEĞİŞİMİ
    if metrics.volume_change >= 100:
        score += 12
        reasons.append("⚡ Son kapanan mumda hacim patlaması")
    elif metrics.volume_change >= 50:
        score += 8
        reasons.append("⚡ Hacim hızlanıyor")
    elif metrics.volume_change >= 25:
        score += 4
        reasons.append("📈 Hacim artıyor")

    # HACİM İVMESİ
    if metrics.volume_acceleration >= 100:
        score += 10
        reasons.append("🚀 Hacim ivmesi çok güçlü")
    elif metrics.volume_acceleration >= 50:
        score += 7
        reasons.append("🔥 Hacim ivmesi yükseliyor")
    elif metrics.volume_acceleration >= 25:
        score += 3
        reasons.append("📈 Hacim ivmesi pozitif")

    # ALICI BASKISI
    if metrics.buy_pressure >= 68:
        score += 18
        reasons.append("🐋 Çok güçlü alıcı baskısı")
    elif metrics.buy_pressure >= 63:
        score += 14
        reasons.append("🟢 Güçlü alıcı baskısı")
    elif metrics.buy_pressure >= 58:
        score += 9
        reasons.append("🟢 Alıcı baskısı pozitif")
    elif metrics.buy_pressure >= 54:
        score += 4
        reasons.append("🟡 Alıcı baskısı yükseliyor")

    # SON 3 MUM BASKISI
    if metrics.average_pressure >= 62:
        score += 8
        reasons.append("🐋 Son 3 mumda güçlü alıcı baskısı")
    elif metrics.average_pressure >= 57:
        score += 5
        reasons.append("🟢 Son 3 mum baskısı pozitif")

    # AÇIK MUM ERKEN UYARI
    if metrics.live_volume_ratio >= 2:
        score += 8
        reasons.append("⚡ Açık 5 dk mumunda olağandışı hacim")
    elif metrics.live_volume_ratio >= 1.3:
        score += 4
        reasons.append("📈 Açık 5 dk mumunda hacim hızlanıyor")

    if metrics.live_buy_pressure >= 63:
        score += 7
        reasons.append("🟢 Açık mumda güçlü alıcı baskısı")
    elif metrics.live_buy_pressure >= 56:
        score += 3
        reasons.append("🟡 Açık mumda alıcı baskısı artıyor")

    if metrics.live_momentum_5m >= 0.5:
        score += 4
        reasons.append("⚡ Fiyat canlı mumda yukarı hareket ediyor")

    # MOMENTUM
    if 0.2 <= metrics.momentum_5m <= 2.5:
        score += 9
        reasons.append("🎯 5 dk hareket erken aşamada")
    elif 2.5 < metrics.momentum_5m <= 4.5:
        score += 5
        reasons.append("📈 5 dk momentum güçleniyor")
    elif metrics.momentum_5m > 6:
        score -= 6
        reasons.append("⏰ 5 dk hareket fazla hızlandı")

    if 0 < metrics.momentum_15m < 4:
        score += 8
        reasons.append("🎯 15 dk hareket erken aşamada")
    elif 4 <= metrics.momentum_15m < 7:
        score += 4
        reasons.append("📈 15 dk momentum güçleniyor")
    elif metrics.momentum_15m >= 10:
        score -= 8
        reasons.append("⏰ 15 dk hareket ilerlemiş")

    if 0 < metrics.momentum_30m < 7:
        score += 5
        reasons.append("📈 30 dk kontrollü yükseliş")
    elif metrics.momentum_30m >= 12:
        score -= 8
        reasons.append("⏰ 30 dk hareket fazla ilerlemiş")

    # RSI
    if 42 <= metrics.rsi <= 62:
        score += 10
