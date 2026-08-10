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
#
# V6 ana hedef:
#   "Zaten yükselmiş coine yüksek puan verme" problemini azaltmak
#   ve hareketin ilk ivmesini daha erken yakalamak.
#
# V5'e göre:
#   - Gerçek EMA kullanımı
#   - 24h fiyat değişimi
#   - Lokal fiyat konumu / extension filtresi
#   - Açık 5m mum analizi
#   - 1m mikro momentum
#   - Spot + Futures hacim birlikte değerlendirme
#   - OI değişimi
#   - Funding
#   - Derivative bonus sınırlandırıldı
#   - Overextended coin için skor tavanı
#   - İki aşamalı analiz ile gereksiz API yükü azaltıldı
#   - OI referansı SQLite'ta kalıcı
#   - Cooldown SQLite'ta kalıcı
#   - Binance retry/backoff
#   - Telegram retry/backoff
#   - Sağlık endpoint'i
# ============================================================


# ============================================================
# CONFIG
# ============================================================

@dataclass(frozen=True)
class Config:

    min_volume: float = field(
        default_factory=lambda: float(
            os.getenv("MIN_VOLUME_USDT", "750000")
        )
    )

    scan_interval: int = field(
        default_factory=lambda: int(
            os.getenv("SCAN_INTERVAL", "300")
        )
    )

    workers: int = field(
        default_factory=lambda: int(
            os.getenv("MAX_WORKERS", "8")
        )
    )

    early: int = field(
        default_factory=lambda: int(
            os.getenv("EARLY_SCORE", "68")
        )
    )

    strong: int = field(
        default_factory=lambda: int(
            os.getenv("STRONG_SCORE", "80")
        )
    )

    whale: int = field(
        default_factory=lambda: int(
            os.getenv("WHALE_SCORE", "90")
        )
    )

    max_signals: int = field(
        default_factory=lambda: int(
            os.getenv("MAX_SIGNALS_PER_SCAN", "5")
        )
    )

    cooldown: int = field(
        default_factory=lambda: int(
            os.getenv("SIGNAL_COOLDOWN", "7200")
        )
    )

    timeout: int = field(
        default_factory=lambda: int(
            os.getenv("REQUEST_TIMEOUT", "8")
        )
    )

    db: str = field(
        default_factory=lambda: os.getenv(
            "STATE_DB_PATH",
            "balina_v6.db"
        )
    )

    oi_staleness_factor: int = field(
        default_factory=lambda: int(
            os.getenv("OI_STALENESS_FACTOR", "3")
        )
    )

    # V6 erken hareket ayarları
    base_gate: int = field(
        default_factory=lambda: int(
            os.getenv("BASE_GATE", "42")
        )
    )

    derivative_max_bonus: int = field(
        default_factory=lambda: int(
            os.getenv("DERIVATIVE_MAX_BONUS", "25")
        )
    )

    # Çok yükselmiş coinlerde skor tavanı
    overextended_score_cap: int = field(
        default_factory=lambda: int(
            os.getenv("OVEREXTENDED_SCORE_CAP", "67")
        )
    )

    # 24h değişim
    extension_24h_pct: float = field(
        default_factory=lambda: float(
            os.getenv("EXTENSION_24H_PCT", "18")
        )
    )

    # Lokal high'a yakınlık
    local_high_position: float = field(
        default_factory=lambda: float(
            os.getenv("LOCAL_HIGH_POSITION", "0.88")
        )
    )


CFG = Config()


# ============================================================
# CREDENTIALS
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


# ============================================================
# BINANCE
# ============================================================

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


LEVERAGED_SUFFIXES = (
    "UPUSDT",
    "DOWNUSDT",
    "BULLUSDT",
    "BEARUSDT",
)


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("balina-v6")


# ============================================================
# HTTP SESSION
# ============================================================

def build_session() -> requests.Session:

    s = requests.Session()

    retry_strategy = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.7,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=[
            "GET",
            "POST",
        ],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        pool_connections=32,
        pool_maxsize=32,
        max_retries=retry_strategy,
    )

    s.mount("https://", adapter)
    s.mount("http://", adapter)

    s.headers.update({
        "User-Agent": "BalinaRadari-V6/1.0"
    })

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
    }


# ============================================================
# API
# ============================================================

def api(
    base: str,
    path: str,
    params: Optional[dict] = None
) -> Any:

    response = S.get(
        base + path,
        params=params,
        timeout=CFG.timeout,
    )

    response.raise_for_status()

    return response.json()


def telegram(text: str) -> bool:

    if not TOKEN or not CHAT:
        log.error(
            "Telegram credential eksik."
        )
        return False

    try:

        response = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT,
                "text": text,
            },
            timeout=CFG.timeout,
        )

        response.raise_for_status()

        result = response.json()

        return bool(result.get("ok"))

    except Exception as error:

        log.error(
            "Telegram hatası: %s",
            error
        )

        return False


def tickers(base: str) -> List[dict]:

    try:

        path = (
            "/api/v3/ticker/24hr"
            if base == SPOT
            else "/fapi/v1/ticker/24hr"
        )

        result = api(base, path)

        return result if isinstance(result, list) else []

    except Exception as error:

        log.error(
            "Ticker hatası (%s): %s",
            base,
            error
        )

        return []


def klines(
    base: str,
    symbol: str,
    interval: str,
    limit: int = 80
) -> List[list]:

    try:

        path = (
            "/api/v3/klines"
            if base == SPOT
            else "/fapi/v1/klines"
        )

        result = api(
            base,
            path,
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
        )

        return result if isinstance(result, list) else []

    except Exception as error:

        log.debug(
            "%s %s %s kline hatası: %s",
            symbol,
            interval,
            base,
            error,
        )

        return []


def open_interest(
    symbol: str
) -> Optional[float]:

    try:

        result = api(
            FUT,
            "/fapi/v1/openInterest",
            {"symbol": symbol},
        )

        return float(
            result["openInterest"]
        )

    except Exception as error:

        log.debug(
            "%s OI hatası: %s",
            symbol,
            error
        )

        return None


def funding(
    symbol: str
) -> Optional[float]:

    try:

        result = api(
            FUT,
            "/fapi/v1/premiumIndex",
            {"symbol": symbol},
        )

        return float(
            result["lastFundingRate"]
        )

    except Exception as error:

        log.debug(
            "%s funding hatası: %s",
            symbol,
            error
        )

        return None


# ============================================================
# MATH
# ============================================================

def pct(
    old: Optional[float],
    new: Optional[float]
) -> float:

    if (
        old is None
        or new is None
        or old <= 0
    ):
        return 0.0

    return (
        (new - old) / old
    ) * 100


def ratio(
    a: float,
    b: float
) -> float:

    if b <= 0:
        return 0.0

    return a / b


def clamp(
    value: float
) -> int:

    return max(
        0,
        min(
            100,
            int(round(value))
        )
    )


def calculate_rsi(
    closes: List[float],
    period: int = 14
) -> Optional[float]:

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):

        change = (
            closes[i] -
            closes[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains[:period]) /
        period
    )

    avg_loss = (
        sum(losses[:period]) /
        period
    )

    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


def ema(
    values: List[float],
    period: int
) -> Optional[float]:

    if len(values) < period:
        return None

    multiplier = 2 / (
        period + 1
    )

    result = (
        sum(values[:period]) /
        period
    )

    for value in values[period:]:

        result = (
            (value - result)
            * multiplier
        ) + result

    return result


# ============================================================
# DATABASE
# ============================================================

class DB:

    def __init__(
        self,
        path: str
    ):

        self.path = path
        self.lock = Lock()

        with self.lock, sqlite3.connect(
            self.path
        ) as connection:

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state (
                    symbol TEXT PRIMARY KEY,
                    sent REAL NOT NULL,
                    score REAL
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS oi (
                    symbol TEXT PRIMARY KEY,
                    value REAL NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    score REAL,
                    ts REAL
                )
                """
            )

    def get_oi_reference(
        self,
        symbol: str
    ) -> Optional[float]:

        with self.lock, sqlite3.connect(
            self.path
        ) as connection:

            row = connection.execute(
                """
                SELECT value, ts
                FROM oi
                WHERE symbol=?
                """,
                (symbol,),
            ).fetchone()

        if row is None:
            return None

        value, timestamp = row

        max_age = (
            CFG.scan_interval *
            CFG.oi_staleness_factor
        )

        if (
            time.time() -
            timestamp
        ) > max_age:

            return None

        return float(value)

    def put_oi(
        self,
        symbol: str,
        value: Optional[float]
    ) -> None:

        if value is None:
            return

        with self.lock, sqlite3.connect(
            self.path
        ) as connection:

            connection.execute(
                """
                INSERT INTO oi
                (symbol, value, ts)
                VALUES (?, ?, ?)

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

    def is_on_cooldown(
        self,
        symbol: str
    ) -> bool:

        with self.lock, sqlite3.connect(
            self.path
        ) as connection:

            row = connection.execute(
                """
                SELECT sent
                FROM state
                WHERE symbol=?
                """,
                (symbol,),
            ).fetchone()

        if row is None:
            return False

        return (
            time.time() - row[0]
            < CFG.cooldown
        )

    def mark_sent(
        self,
        symbol: str,
        score: float
    ) -> None:

        with self.lock, sqlite3.connect(
            self.path
        ) as connection:

            connection.execute(
                """
                INSERT INTO state
                (symbol, sent, score)
                VALUES (?, ?, ?)

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


DBS = DB(CFG.db)


# ============================================================
# CANDIDATES
# ============================================================

@dataclass
class Candidate:

    symbol: str
    change_24h: float
    spot_volume: float
    futures_volume: float


def collect_candidates(
    spot_tickers: List[dict],
    futures_tickers: List[dict]
) -> List[Candidate]:

    futures_map = {
        x.get("symbol"): x
        for x in futures_tickers
    }

    result = []

    for spot in spot_tickers:

        symbol = spot.get(
            "symbol",
            ""
        )

        if not symbol.endswith("USDT"):
            continue

        if symbol in EXCLUDED:
            continue

        if any(
            symbol.endswith(x)
            for x in LEVERAGED_SUFFIXES
        ):
            continue

        futures = futures_map.get(
            symbol
        )

        if not futures:
            continue

        try:

            spot_volume = float(
                spot.get(
                    "quoteVolume",
                    0
                )
            )

            futures_volume = float(
                futures.get(
                    "quoteVolume",
                    0
                )
            )

            change_24h = float(
                spot.get(
                    "priceChangePercent",
                    0
                )
            )

        except (
            TypeError,
            ValueError
        ):

            continue

        if spot_volume < CFG.min_volume:
            continue

        if futures_volume < CFG.min_volume:
            continue

        result.append(
            Candidate(
                symbol=symbol,
                change_24h=change_24h,
                spot_volume=spot_volume,
                futures_volume=futures_volume,
            )
        )

    return result


# ============================================================
# METRICS
# ============================================================

@dataclass
class BaseMetrics:

    price: float

    volume_ratio: float
    futures_volume_ratio: float
    volume_acceleration: float

    buy_pressure: float

    momentum_1m: float
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


@dataclass
class DerivativeMetrics:

    oi_change: float
    oi_available: bool
    funding: Optional[float]


# ============================================================
# BASE ANALYSIS
# ============================================================

def extract_base_metrics(
    candidate: Candidate
) -> Tuple[
    Optional[BaseMetrics],
    Optional[Dict[str, Any]]
]:

    symbol = candidate.symbol

    # 5m + 15m + Futures 5m
    spot_5m = klines(
        SPOT,
        symbol,
        "5m",
        80
    )

    spot_15m = klines(
        SPOT,
        symbol,
        "15m",
        80
    )

    futures_5m = klines(
        FUT,
        symbol,
        "5m",
        80
    )

    if (
        len(spot_5m) < 60
        or len(spot_15m) < 60
        or len(futures_5m) < 60
    ):

        return None, {
            "status": "insufficient"
        }

    closed_5m = spot_5m[:-1]
    live_5m = spot_5m[-1]

    closed_15m = spot_15m[:-1]
    closed_futures_5m = futures_5m[:-1]

    closes_5m = [
        float(x[4])
        for x in closed_5m
    ]

    volumes_5m = [
        float(x[7])
        for x in closed_5m
    ]

    taker_buy_5m = [
        float(x[10])
        for x in closed_5m
    ]

    closes_15m = [
        float(x[4])
        for x in closed_15m
    ]

    futures_volumes = [
        float(x[7])
        for x in closed_futures_5m
    ]

    if len(closes_5m) < 20:
        return None, {
            "status": "insufficient"
        }

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    closed_price = closes_5m[-1]

    live_price = float(
        live_5m[4]
    )

    price = (
        live_price
        if live_price > 0
        else closed_price
    )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    historical_volumes = (
        volumes_5m[-25:-1]
    )

    if len(historical_volumes) < 10:
        return None, {
            "status": "insufficient"
        }

    avg_volume = (
        sum(historical_volumes)
        / len(historical_volumes)
    )

    historical_futures = (
        futures_volumes[-25:-1]
    )

    avg_futures_volume = (
        sum(historical_futures)
        / len(historical_futures)
    )

    if (
        avg_volume <= 0
        or avg_futures_volume <= 0
    ):

        return None, {
            "status": "insufficient"
        }

    current_volume = volumes_5m[-1]

    futures_current_volume = (
        futures_volumes[-1]
    )

    volume_ratio = ratio(
        current_volume,
        avg_volume
    )

    futures_volume_ratio = ratio(
        futures_current_volume,
        avg_futures_volume
    )

    # --------------------------------------------------------
    # VOLUME ACCELERATION
    # -------------------------
Thread(target=loop, daemon=True, name="balina-v5").start()
