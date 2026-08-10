import os
import time
import sqlite3
import logging
from dataclasses import dataclass
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V8
# ULTRA EARLY CONFLUENCE ENGINE
# LONG + SHORT / EARLY + CONFIRMED
# ============================================================

VERSION = "V8.0"

@dataclass(frozen=True)
class Config:
    min_volume: float = float(os.getenv("MIN_VOLUME_USDT", "1000000"))
    scan_interval: int = int(os.getenv("SCAN_INTERVAL", "300"))
    workers: int = int(os.getenv("MAX_WORKERS", "8"))

    early_threshold: int = int(os.getenv("EARLY_THRESHOLD", "76"))
    confirmed_threshold: int = int(os.getenv("CONFIRMED_THRESHOLD", "86"))

    max_signals: int = int(os.getenv("MAX_SIGNALS_PER_SCAN", "4"))
    cooldown: int = int(os.getenv("SIGNAL_COOLDOWN", "7200"))
    timeout: int = int(os.getenv("REQUEST_TIMEOUT", "8"))

    db: str = os.getenv("STATE_DB_PATH", "balina_v8.db")

    # Hareket bundan fazla ilerlediyse EARLY sinyal engellenir.
    max_early_5m: float = float(os.getenv("MAX_EARLY_5M", "4.5"))
    max_early_15m: float = float(os.getenv("MAX_EARLY_15M", "8.0"))
    max_early_30m: float = float(os.getenv("MAX_EARLY_30M", "12.0"))

    # Resistance/support'a maksimum uzaklık.
    structure_distance: float = float(os.getenv("STRUCTURE_DISTANCE", "2.5"))

    # OI referans yaş sınırı.
    oi_max_age: int = int(os.getenv("OI_MAX_AGE", "1800"))


CFG = Config()

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
}

STABLE_BASES = (
    "UPUSDT",
    "DOWNUSDT",
    "BULLUSDT",
    "BEARUSDT",
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("balina-v8")


# ============================================================
# HTTP SESSION
# ============================================================

def build_session() -> requests.Session:
    s = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        pool_connections=32,
        pool_maxsize=32,
        max_retries=retry,
    )

    s.mount("https://", adapter)
    s.mount("http://", adapter)

    s.headers.update({
        "User-Agent": "BalinaRadari-V8-Ultra/1.0"
    })

    return s


S = build_session()


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V8 Ultra Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V8 Ultra",
        "version": VERSION,
        "early_threshold": CFG.early_threshold,
        "confirmed_threshold": CFG.confirmed_threshold,
    }


# ============================================================
# API
# ============================================================

def api(
    base: str,
    path: str,
    params: Optional[dict] = None,
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
        log.warning("Telegram TOKEN veya CHAT_ID eksik.")
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

        data = response.json()

        if not data.get("ok"):
            log.error("Telegram API başarısız: %s", data)
            return False

        return True

    except Exception as e:
        log.error("Telegram hatası: %s", e)
        return False


def tickers(base: str) -> List[dict]:

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


def klines(
    base: str,
    symbol: str,
    interval: str,
    limit: int,
) -> List[list]:

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


def open_interest(symbol: str) -> Optional[float]:

    try:
        data = api(
            FUT,
            "/fapi/v1/openInterest",
            {"symbol": symbol},
        )

        return float(data["openInterest"])

    except Exception as e:
        log.debug(
            "%s OI hatası: %s",
            symbol,
            e,
        )
        return None


def funding(symbol: str) -> Optional[float]:

    try:
        data = api(
            FUT,
            "/fapi/v1/premiumIndex",
            {"symbol": symbol},
        )

        return float(data["lastFundingRate"])

    except Exception:
        return None


# ============================================================
# MATHEMATICS
# ============================================================

def pct(
    old: Optional[float],
    new: Optional[float],
) -> float:

    if old is None or new is None or old <= 0:
        return 0.0

    return ((new - old) / old) * 100.0


def clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def distance_pct(
    price: float,
    level: float,
) -> float:

    if price <= 0 or level <= 0:
        return 999.0

    return abs(price - level) / price * 100.0


def ema(
    values: List[float],
    period: int,
) -> Optional[float]:

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (
            (value - result) * multiplier
            + result
        )

    return result


def rsi(
    closes: List[float],
    period: int = 14,
) -> Optional[float]:

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(closes)):

        diff = closes[i] - closes[i - 1]

        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

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

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def macd(
    closes: List[float],
) -> Tuple[
    Optional[float],
    Optional[float],
    Optional[float],
]:

    if len(closes) < 50:
        return None, None, None

    def ema_series(
        values: List[float],
        period: int,
    ) -> List[float]:

        if len(values) < period:
            return []

        multiplier = 2 / (period + 1)

        current = sum(values[:period]) / period
        result = [current]

        for value in values[period:]:
            current = (
                (value - current) * multiplier
                + current
            )
            result.append(current)

        return result

    fast = ema_series(closes, 12)
    slow = ema_series(closes, 26)

    if not fast or not slow:
        return None, None, None

    length = min(len(fast), len(slow))

    line = [
        fast[-length + i] - slow[-length + i]
        for i in range(length)
    ]

    signal = ema_series(line, 9)

    if not signal:
        return None, None, None

    macd_value = line[-1]
    signal_value = signal[-1]
    histogram = macd_value - signal_value

    return (
        macd_value,
        signal_value,
        histogram,
    )


# ============================================================
# DATABASE
# ============================================================

class DB:

    def __init__(self, path: str):

        self.path = path
        self.lock = Lock()

        with self.lock, sqlite3.connect(path) as conn:

            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL,
                    score REAL,
                    direction TEXT
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oi(
                    symbol TEXT PRIMARY KEY,
                    value REAL,
                    ts REAL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS oi_history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    value REAL,
                    ts REAL
                )
                """
            )

    # --------------------------------------------------------

    def oi_reference(
        self,
        symbol: str,
    ) -> Optional[float]:

        with self.lock, sqlite3.connect(self.path) as conn:

            row = conn.execute(
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

        if time.time() - timestamp > CFG.oi_max_age:
            return None

        return float(value)

    # --------------------------------------------------------

    def previous_oi(
        self,
        symbol: str,
    ) -> Optional[float]:

        with self.lock, sqlite3.connect(self.path) as conn:

            row = conn.execute(
                """
                SELECT value
                FROM oi_history
                WHERE symbol=?
                ORDER BY ts DESC
                LIMIT 2
                """,
                (symbol,),
            ).fetchall()

        if len(row) < 2:
            return None

        return float(row[1][0])

    # --------------------------------------------------------

    def put_oi(
        self,
        symbol: str,
        value: Optional[float],
    ):

        if value is None:
            return

        now = time.time()

        with self.lock, sqlite3.connect(self.path) as conn:

            conn.execute(
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
                    now,
                ),
            )

            conn.execute(
                """
                INSERT INTO oi_history(symbol,value,ts)
                VALUES(?,?,?)
                """,
                (
                    symbol,
                    value,
                    now,
                ),
            )

            # Coin başına gereksiz tarihçe büyümesini engelle.
            conn.execute(
                """
                DELETE FROM oi_history
                WHERE symbol=?
                AND id NOT IN (
                    SELECT id
                    FROM oi_history
                    WHERE symbol=?
                    ORDER BY ts DESC
                    LIMIT 20
                )
                """,
                (
                    symbol,
                    symbol,
                ),
            )

    # --------------------------------------------------------

    def cooldown(
        self,
        symbol: str,
        direction: str,
    ) -> bool:

        with self.lock, sqlite3.connect(self.path) as conn:

            row = conn.execute(
                """
                SELECT sent, direction
                FROM state
                WHERE symbol=?
                """,
                (symbol,),
            ).fetchone()

        if not row:
            return False

        sent, old_direction = row

        # Ters yöne dönüşte cooldown'u bypass et.
        if old_direction and old_direction != direction:
            return False

        return (
            time.time() - sent
            < CFG.cooldown
        )

    # --------------------------------------------------------

    def mark_sent(
        self,
        symbol: str,
        score: int,
        direction: str,
    ):

        with self.lock, sqlite3.connect(self.path) as conn:

            conn.execute(
                """
                INSERT INTO state(
                    symbol,
                    sent,
                    score,
                    direction
                )
                VALUES(?,?,?,?)
                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    direction=excluded.direction
                """,
                (
                    symbol,
                    time.time(),
                    score,
                    direction,
                ),
            )


DBS = DB(CFG.db)


# ============================================================
# CANDIDATES
# ============================================================

def candidates(
    spot: List[dict],
    futures: List[dict],
) -> List[str]:

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
            for x in STABLE_BASES
        ):
            continue

        future = futures_map.get(symbol)

        if not future:
            continue

        try:

            spot_volume = float(
                item.get("quoteVolume", 0)
            )

            futures_volume = float(
                future.get("quoteVolume", 0)
            )

        except (TypeError, ValueError):
            continue

        if spot_volume < CFG.min_volume:
            continue

        if futures_volume < CFG.min_volume:
            continue

        result.append(symbol)

    return result


# ============================================================
# MARKET DATA
# ============================================================

@dataclass
class Market:

    price: float

    m1: float
    m5: float
    m15: float
    m30: float

    volume_ratio: float
    futures_volume_ratio: float

    taker_buy: float
    taker_delta: float

    rsi: float

    ema20: float
    ema50: float
    ema200: float

    macd_hist: float

    resistance: float
    support: float

    resistance_distance: float
    support_distance: float


# ============================================================
# MARKET ANALYSIS
# ============================================================

def extract_market(
    symbol: str,
) -> Optional[Market]:

    # 1m erken hareket
    k1 = klines(
        SPOT,
        symbol,
        "1m",
        120,
    )

    # 5m momentum
    k5 = klines(
        SPOT,
        symbol,
        "5m",
        120,
    )

    # 15m ana yapı
    k15 = klines(
        SPOT,
        symbol,
        "15m",
        220,
    )

    # Futures volume
    f5 = klines(
        FUT,
        symbol,
        "5m",
        120,
    )

    if (
        len(k1) < 70
        or len(k5) < 70
        or len(k15) < 210
        or len(f5) < 70
    ):
        return None

    # Sadece kapanmış mumları indikatörlerde kullan.
    c1 = k1[:-1]
    c5 = k5[:-1]
    c15 = k15[:-1]
    cf5 = f5[:-1]

    closes1 = [
        float(x[4])
        for x in c1
    ]

    closes5 = [
        float(x[4])
        for x in c5
    ]

    closes15 = [
        float(x[4])
        for x in c15
    ]

    highs15 = [
        float(x[2])
        for x in c15
    ]

    lows15 = [
        float(x[3])
        for x in c15
    ]

    volumes5 = [
        float(x[7])
        for x in c5
    ]

    futures_volumes5 = [
        float(x[7])
        for x in cf5
    ]

    taker_buy5 = [
        float(x[10])
        for x in c5
    ]

    price = float(
        k1[-1][4]
    )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    m1 = pct(
        closes1[-2],
        closes1[-1],
    )

    m5 = pct(
        closes5[-2],
        closes5[-1],
    )

    m15 = pct(
        closes15[-2],
        closes15[-1],
    )

    m30 = pct(
        closes15[-3],
        closes15[-1],
    )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    avg_volume = (
        sum(volumes5[-25:-1])
        / 24
    )

    avg_futures_volume = (
        sum(futures_volumes5[-25:-1])
        / 24
    )

    if (
        avg_volume <= 0
        or avg_futures_volume <= 0
    ):
        return None

    volume_ratio = (
        volumes5[-1]
        / avg_volume
    )

    futures_volume_ratio = (
        futures_volumes5[-1]
        / avg_futures_volume
    )

    taker_buy = (
        taker_buy5[-1]
        / volumes5[-1]
        * 100
        if volumes5[-1] > 0
        else 50
    )

    previous_buy = (
        sum(taker_buy5[-4:-1])
        / 3
        / (
            sum(volumes5[-4:-1])
            / 3
        )
        * 100
        if sum(volumes5[-4:-1]) > 0
        else 50
    )

    taker_delta = (
        taker_buy
        - previous_buy
    )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    ema20 = ema(
        closes15,
        20,
    )

    ema50 = ema(
        closes15,
        50,
    )

    ema200 = ema(
        closes15,
        200,
    )

    if (
        ema20 is None
        or ema50 is None
        or ema200 is None
    ):
        return None

    # --------------------------------------------------------
    # RSI / MACD
    # --------------------------------------------------------

    rsi_value = rsi(
        closes5
    )

    if rsi_value is None:
        return None

    _, _, macd_hist = macd(
        closes5
    )

    if macd_hist is None:
        return None

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    lookback = 50

    recent_highs = highs15[
        -lookback - 2:
        -2
    ]

    recent_lows = lows15[
        -lookback - 2:
        -2
]

    resistance = max(
        recent_highs
    )
support = min(
        recent_lows
    )

    resistance_distance = (
        (resistance - price)
        / price
        * 100
    )

    support_distance = (
        (price - support)
        / price
        * 100
    )return Market(
        price=price,

        m1=m1,
        m5=m5,
        m15=m15,
        m30=m30,volume_ratio=volume_ratio,
        futures_volume_ratio=futures_volume_ratio,

        taker_buy=taker_buy,
        taker_delta=taker_delta,

        rsi=rsi_value,

        ema20=ema20,
        ema50=ema50,
        ema200=ema200,

        macd_hist=macd_hist,
        resistance=resistance,
        support=support,

        resistance_distance=resistance_distance,
        support_distance=support_distance,
    )
# ============================================================
# OI ANALYSIS
# ============================================================

@dataclass
class Derivatives:

    oi_change: float
    oi_acceleration: float

    funding: float

    available: bool
    def derivative_data(
    symbol: str,
) -> Derivatives:

    current = open_interest(
        symbol
    )

    reference = DBS.oi_reference(
        symbol
    )

    previous = DBS.previous_oi(
        symbol
    )
    if (
        reference is not None
        and current is not None
    ):
        oi_change = pct(
            reference,
            current,
        )
    else:
        oi_change = 0.0

    if (
        previous is not None
        and current is not None
    ):
        recent_change = pct(
            previous,
            current,
        )recent_change = pct(
            previous,
            current,
        )
    else:
        recent_change = 0.0

    # Son ölçüm ile mevcut hareket arasındaki hız farkı.
    oi_acceleration = (
        recent_change
        - oi_change
    )

    fund = funding(
        symbol
    )DBS.put_oi(
        symbol,
        current,
    )

    return Derivatives(
        oi_change=oi_change,
        oi_acceleration=oi_acceleration,
        funding=fund or 0.0,
        available=(
            current is not None
        ),
    )

# ============================================================
# LATE MOVE FILTER
# ============================================================

def too_late(
    m: Market,
) -> bool:

    if m.m5 >= CFG.max_early_5m:
        return True

    if m.m15 >= CFG.max_early_15m:
        return True
        if m.m30 >= CFG.max_early_30m:
        return True

    return False

# ============================================================
# LONG SCORE
# ============================================================

def score_long(
    m: Market,
    d: Derivatives,
) -> Tuple[int, List[str], str]:

    score = 0.0
    evidence = []
    # --------------------------------------------------------
    # EARLY MOMENTUM
    # --------------------------------------------------------

    if 0.15 <= m.m1 <= 1.2:
        score += 5
        evidence.append(
            "⚡ 1m kontrollümomentum"
        )

    if 0.2 <= m.m5 <= 2.5:
        score += 8
        evidence.append(
            "🎯 5m erken momentum"
        )

    elif 2.5 < m.m5 <= 4.5:
        score += 4
        evidence.append(
            "📈 5m momentum güçleniyor"
        )
        if 0 < m.m15 <= 3.5:
        score += 6
        evidence.append(
            "📈 15m erken hareket"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if m.volume_ratio >= 4:
        score += 15
        evidence.append(
            f"🐋 Spot hacmi {m.volume_ratio:.1f}x"
        )

    elif m.volume_ratio >= 3:
        score += 12
        evidence.append(
            f"🔥 Spot hacmi {m.volume_ratio:.1f}x"
        )

    elif m.volume_ratio >= 2:
        score += 8evidence.append(
            f"📊 Spot hacmi {m.volume_ratio:.1f}x"
        )

    if m.futures_volume_ratio >= 3:
        score += 7
        evidence.append(
            f"⚡ Futures hacmi {m.futures_volume_ratio:.1f}x"
        )

    elif m.futures_volume_ratio >= 2:
        score += 5
        evidence.append(
            f"⚡ Futures hacmi {m.futures_volume_ratio:.1f}x"
        )
        # --------------------------------------------------------
    # TAKER FLOW
    # --------------------------------------------------------

    if m.taker_buy >= 68:
        score += 15
        evidence.append(
            f"🐋 Çok güçlü alıcı akışı %{m.taker_buy:.1f}"
        )elif m.taker_buy >= 58:
        score += 7
        evidence.append(
            f"🟢 Pozitif alıcı akışı %{m.taker_buy:.1f}"
        )

    if m.taker_delta >= 3:
        score += 5
        evidence.append(
            "🚀 Taker buy ivmesi artıyor"
        )

 # --------------------------------------------------------
    # OI
    # --------------------------------------------------------

    if d.available:

        if d.oi_change >= 8:
            score += 12
            evidence.append(
                f"🐋 OI güçlü artıyor +%{d.oi_change:.1f}"
            )

        elif d.oi_change >= 5:
            score += 9
            evidence.append(
                f"📈 OI artıyor +%{d.oi_change:.1f}"
            )elif d.oi_change >= 3:
            score += 6
            evidence.append(
                f"📊 OI yükseliyor +%{d.oi_change:.1f}"
            )

        if d.oi_acceleration > 1:
            score += 5
            evidence.append(
                "🚀 OI ivmesi hızlanıyor"
            )

    # --------------------------------------------------------
    # PRICE + OI STRUCTURE
    # --------------------------------------------------------
    if (
        m.m15 >= 0
        and d.oi_change >= 3
    ):
        score += 6
        evidence.append(
            "🔗 Fiyat + OI uyumlu"
        )
        # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------
if m.price > m.ema20:
        score += 3
        evidence.append(
            "📈 Fiyat EMA20 üzerinde"
        )
    if m.ema20 > m.ema50:
        score += 4
        evidence.append(
            "📈 Kısa vadeli trend yukarı"
        )

    if m.price > m.ema200:
        score += 3
        evidence.append(
            "🌟 EMA200 üzerinde"
        )
        # --------------------------------------------------------
    # MACD
    # --------------------------------------------------------

    if m.macd_hist > 0:
        score += 6
        evidence.append(
            "📊 MACD pozitif"
        )

    #--------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if 48 <= m.rsi <= 67:
        score += 7
        evidence.append(
            f"📊 RSI sağlıklı ivme {m.rsi:.1f}"
        )

    elif 67 < m.rsi <= 73:
        score += 2

    elif m.rsi > 78:
        score -= 10
        evidence.append(
            "⏰ RSI aşırı yükseldi"
        )
        # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if (
        m.price > m.resistance
        and m.volume_ratio >= 2
    ):
        score += 15
        evidence.append(
            "💥 Direnç hacimle kırıldı"
        )
        
