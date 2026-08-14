import os
import time
import sqlite3
import logging
import sys

from threading import Thread, Lock
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed
)

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask


# =========================================================
# V22 | ANA KONFİGÜRASYON
# =========================================================

BASE = os.getenv(
    "BINANCE_TR_BASE",
    "https://api.binance.me"
)

SCAN_INTERVAL = int(
    os.getenv(
        "SCAN_INTERVAL",
        "30"
    )
)

WORKERS = int(
    os.getenv(
        "MAX_WORKERS",
        "10"
    )
)

MAX_SIGNALS = int(
    os.getenv(
        "MAX_SIGNALS_PER_SCAN",
        "5"
    )
)

COOLDOWN = int(
    os.getenv(
        "SIGNAL_COOLDOWN",
        "1200"
    )
)

MIN_QUOTE_VOLUME = float(
    os.getenv(
        "MIN_QUOTE_VOLUME_TRY",
        "1000000"
    )
)

SHORTLIST = int(
    os.getenv(
        "SHORTLIST_SIZE",
        "80"
    )
)

TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "8"
    )
)

DB_PATH = os.getenv(
    "STATE_DB_PATH",
    "balina_v22.db"
)

OUTCOME_WINDOW = int(
    os.getenv(
        "OUTCOME_WINDOW",
        "900"
    )
)


# =========================================================
# TELEGRAM
# =========================================================

TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

CHAT = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# =========================================================
# V22 | UZUN VADELİ TREND EŞİKLERİ
#
# Bütün eşikler tek yerden okunuyor.
# message() içinde sabit -20 / -50 gibi
# gizli değer kullanılmayacak.
# =========================================================

LONG_TERM_CACHE_TTL = int(
    os.getenv(
        "LONG_TERM_CACHE_TTL",
        "900"
    )
)

LT30_MILD = float(
    os.getenv(
        "LT30_MILD",
        "-20"
    )
)

LT30_STRONG = float(
    os.getenv(
        "LT30_STRONG",
        "-35"
    )
)

LT90_MILD = float(
    os.getenv(
        "LT90_MILD",
        "-30"
    )
)

LT90_STRONG = float(
    os.getenv(
        "LT90_STRONG",
        "-50"
    )
)

LT90_EXTREME = float(
    os.getenv(
        "LT90_EXTREME",
        "-65"
    )
)


# =========================================================
# V22 | TRADE COUNT GÜVEN EŞİKLERİ
# =========================================================

MIN_1M_TRADES = int(
    os.getenv(
        "MIN_1M_TRADES",
        "20"
    )
)

MIN_5M_TRADES = int(
    os.getenv(
        "MIN_5M_TRADES",
        "50"
    )
)

TRADE_COUNT_REFERENCE = int(
    os.getenv(
        "TRADE_COUNT_REFERENCE",
        "100"
    )
)


# =========================================================
# V22 | STREAK / SÜREKLİLİK
# =========================================================

STREAK_WINDOW = int(
    os.getenv(
        "STREAK_WINDOW",
        "180"
    )
)

BUY_STREAK_REQUIRED = int(
    os.getenv(
        "BUY_STREAK_REQUIRED",
        "2"
    )
)

VERY_STREAK_REQUIRED = int(
    os.getenv(
        "VERY_STREAK_REQUIRED",
        "2"
    )
)


# =========================================================
# V22 | BTC/TRY PİYASA FİLTRESİ
# =========================================================

MARKET_SYMBOL = os.getenv(
    "MARKET_SYMBOL",
    "BTCTRY"
)

MARKET_MOMENTUM_MINUTES = int(
    os.getenv(
        "MARKET_MOMENTUM_MINUTES",
        "15"
    )
)

MARKET_STRONG_MOVE = float(
    os.getenv(
        "MARKET_STRONG_MOVE",
        "2.0"
    )
)

MARKET_EXTREME_MOVE = float(
    os.getenv(
        "MARKET_EXTREME_MOVE",
        "4.0"
    )
)


# =========================================================
# V22 | ÖNCELİK SIRALAMASI
# =========================================================

TOP_PRIORITY_COUNT = int(
    os.getenv(
        "TOP_PRIORITY_COUNT",
        "5"
    )
)

PRIORITY_MIN_SCORE = int(
    os.getenv(
        "PRIORITY_MIN_SCORE",
        "60"
    )
)


# =========================================================
# V22 | GİRİŞ KALİTESİ
# =========================================================

ENTRY_RSI_HIGH = float(
    os.getenv(
        "ENTRY_RSI_HIGH",
        "78"
    )
)

ENTRY_RSI_EXTREME = float(
    os.getenv(
        "ENTRY_RSI_EXTREME",
        "88"
    )
)

ENTRY_MOMENTUM_HIGH = float(
    os.getenv(
        "ENTRY_MOMENTUM_HIGH",
        "2.5"
    )
)

ENTRY_MOMENTUM_EXTREME = float(
    os.getenv(
        "ENTRY_MOMENTUM_EXTREME",
        "5.0"
    )
)


# =========================================================
# V22 | TRAP
# =========================================================

TRAP_BUYER_LOW = float(
    os.getenv(
        "TRAP_BUYER_LOW",
        "50"
    )
)

TRAP_VOLUME_HIGH = float(
    os.getenv(
        "TRAP_VOLUME_HIGH",
        "1.8"
    )
)

TRAP_MOMENTUM_NEGATIVE = float(
    os.getenv(
        "TRAP_MOMENTUM_NEGATIVE",
        "-1.2"
    )
)


# =========================================================
# V22 | DIŞARIDA BIRAKILACAK MARKETLER
# =========================================================

EXCLUDED = {
    "USDTTRY",
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "BUSDUSDT",
    "DAIUSDT",
}


# =========================================================
# LOG
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    ),
    stream=sys.stdout
)

log = logging.getLogger(
    "balina-v22"
)


# =========================================================
# HTTP SESSION
# =========================================================

def build_session():

    retry_args = dict(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504
        ],
        raise_on_status=False
    )

    try:

        retry = Retry(
            allowed_methods=[
                "GET",
                "POST"
            ],
            **retry_args
        )

    except TypeError:

        retry = Retry(
            method_whitelist=[
                "GET",
                "POST"
            ],
            **retry_args
        )

    session = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=40,
        pool_maxsize=40,
        max_retries=retry
    )

    session.mount(
        "https://",
        adapter
    )

    session.headers.update({
        "User-Agent":
            "BalinaRadari-V22/1.0"
    })

    return session


S = build_session()


# =========================================================
# V22 | CACHE KİLİTLERİ
# =========================================================

DAILY_CACHE = {}

DAILY_CACHE_LOCK = Lock()


MARKET_CACHE = {}

MARKET_CACHE_LOCK = Lock()


# =========================================================
# API
# =========================================================

def api(
    path,
    params=None,
    method="GET",
    payload=None
):

    response = S.request(
        method,
        BASE + path,
        params=params,
        json=payload,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(text):

    if not TOKEN or not CHAT:

        return False

    try:

        response = S.post(
            (
                "https://api.telegram.org/"
                f"bot{TOKEN}/sendMessage"
            ),
            json={
                "chat_id": CHAT,
                "text": text
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        return bool(
            response.json().get(
                "ok"
            )
        )

    except Exception as e:

        log.error(
            "Telegram: %s",
            e
        )

        return False


# =========================================================
# MARKET VERİLERİ
# =========================================================

def tickers():

    try:

        return api(
            "/api/v3/ticker/24hr"
        )

    except Exception as e:

        log.error(
            "Ticker: %s",
            e
        )

        return []


def exchange_info():

    try:

        return api(
            "/api/v3/exchangeInfo"
        )

    except Exception as e:

        log.error(
            "ExchangeInfo: %s",
            e
        )

        return {}


def klines(
    symbol,
    interval,
    limit
):

    try:

        return api(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )

    except Exception as e:

        log.debug(
            "%s %s: %s",
            symbol,
            interval,
            e
        )

        return []


# =========================================================
# TEMEL MATEMATİK
# =========================================================

def avg(values):

    return (
        sum(values) / len(values)
        if values
        else 0.0
    )


def pct(a, b):

    if not a or b is None:

        return 0.0

    return (
        (b - a) / a
    ) * 100


def clamp(value):

    return max(
        0,
        min(
            100,
            int(
                round(value)
            )
        )
    )


# =========================================================
# V22 | GÜNLÜK TREND
#
# ÖNEMLİ:
# Bu fonksiyon analyze() başında çağrılmayacak.
# Önce 5m gate çalışacak.
# Böylece ölü adaylarda gereksiz günlük API
# isteği yapılmayacak.
# =========================================================

def daily_trend(symbol):

    now = time.time()

    with DAILY_CACHE_LOCK:

        cached = DAILY_CACHE.get(
            symbol
        )

        if cached:

            cached_time, cached_data = cached

            if (
                now - cached_time
                <
                LONG_TERM_CACHE_TTL
            ):

                return cached_data


    data = klines(
        symbol,
        "1d",
        100
    )

    if len(data) < 92:

        result = {
            "ok": False,
            "d30": 0.0,
            "d90": 0.0
        }

        with DAILY_CACHE_LOCK:

            DAILY_CACHE[symbol] = (
                now,
                result
            )

        return result


    closed = data[:-1]

    try:

        closes = [
            float(x[4])
            for x in closed
        ]

    except (
        TypeError,
        ValueError
    ):

        return {
            "ok": False,
            "d30": 0.0,
            "d90": 0.0
        }


    if len(closes) < 91:

        return {
            "ok": False,
            "d30": 0.0,
            "d90": 0.0
        }


    current = closes[-1]

    base_30 = closes[-31]

    base_90 = closes[-91]

    d30 = pct(
        base_30,
        current
    )

    d90 = pct(
        base_90,
        current
    )

    result = {
        "ok": True,
        "d30": d30,
        "d90": d90
    }

    with DAILY_CACHE_LOCK:

        DAILY_CACHE[symbol] = (
            now,
            result
        )

    return result
# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if not values:

        return 0.0

    if len(values) < period:

        return avg(values)

    k = 2 / (
        period + 1
    )

    result = avg(
        values[:period]
    )

    for value in values[period:]:

        result = (
            value * k
            +
            result * (1 - k)
        )

    return result


# =========================================================
# RSI
# =========================================================

def rsi(
    values,
    period=14
):

    if len(values) < (
        period + 1
    ):

        return 50.0

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        diff = (
            values[i]
            -
            values[i - 1]
        )

        gains.append(
            max(
                diff,
                0
            )
        )

        losses.append(
            max(
                -diff,
                0
            )
        )

    average_gain = avg(
        gains[-period:]
    )

    average_loss = avg(
        losses[-period:]
    )

    if average_loss == 0:

        return 100.0

    return (
        100
        -
        100 / (
            1
            +
            average_gain
            /
            average_loss
        )
    )


# =========================================================
# MACD
# =========================================================

def macd(values):

    if len(values) < 35:

        return (
            0,
            0,
            0
        )

    macd_values = []

    for i in range(
        26,
        len(values) + 1
    ):

        macd_values.append(
            ema(
                values[:i],
                12
            )
            -
            ema(
                values[:i],
                26
            )
        )

    main = macd_values[-1]

    signal = ema(
        macd_values,
        9
    )

    histogram = (
        main
        -
        signal
    )

    return (
        main,
        signal,
        histogram
    )


# =========================================================
# BOLLINGER BANDS
# =========================================================

def bb(
    values,
    period=20,
    k=2
):

    if len(values) < period:

        return (
            0,
            0,
            0
        )

    sample = values[-period:]

    middle = avg(
        sample
    )

    deviation = (
        avg([
            (
                x
                -
                middle
            ) ** 2
            for x in sample
        ])
    ) ** 0.5

    return (
        middle - k * deviation,
        middle,
        middle + k * deviation
    )


# =========================================================
# ADX
#
# V22'de Wilder tipi hesaplama kullanılıyor.
# Amaç özellikle düşük likiditeli coinlerde
# V21'de görülen yapay 100 ADX durumlarını azaltmak.
# =========================================================

def adx(
    highs,
    lows,
    closes,
    period=14
):

    if len(closes) < (
        period * 2 + 1
    ):

        return (
            0,
            0,
            0
        )

    true_ranges = []
    plus_dm = []
    minus_dm = []

    for i in range(
        1,
        len(closes)
    ):

        high_diff = (
            highs[i]
            -
            highs[i - 1]
        )

        low_diff = (
            lows[i - 1]
            -
            lows[i]
        )

        tr = max(
            highs[i]
            -
            lows[i],

            abs(
                highs[i]
                -
                closes[i - 1]
            ),

            abs(
                lows[i]
                -
                closes[i - 1]
            )
        )

        true_ranges.append(
            tr
        )

        if (
            high_diff > low_diff
            and
            high_diff > 0
        ):

            plus_dm.append(
                high_diff
            )

        else:

            plus_dm.append(
                0.0
            )


        if (
            low_diff > high_diff
            and
            low_diff > 0
        ):

            minus_dm.append(
                low_diff
            )

        else:

            minus_dm.append(
                0.0
            )


    if len(true_ranges) < period:

        return (
            0,
            0,
            0
        )


    # İlk Wilder değerleri.

    atr = sum(
        true_ranges[:period]
    )

    plus_smoothed = sum(
        plus_dm[:period]
    )

    minus_smoothed = sum(
        minus_dm[:period]
    )


    dx_values = []

    last_plus_di = 0.0
    last_minus_di = 0.0


    for i in range(
        period,
        len(true_ranges)
    ):

        if i > period:

            atr = (
                atr
                -
                (
                    atr / period
                )
                +
                true_ranges[i]
            )

            plus_smoothed = (
                plus_smoothed
                -
                (
                    plus_smoothed
                    /
                    period
                )
                +
                plus_dm[i]
            )

            minus_smoothed = (
                minus_smoothed
                -
                (
                    minus_smoothed
                    /
                    period
                )
                +
                minus_dm[i]
            )


        if atr <= 0:

            continue


        plus_di = (
            100
            *
            plus_smoothed
            /
            atr
        )

        minus_di = (
            100
            *
            minus_smoothed
            /
            atr
        )


        denominator = (
            plus_di
            +
            minus_di
        )


        if denominator <= 0:

            dx = 0.0

        else:

            dx = (
                100
                *
                abs(
                    plus_di
                    -
                    minus_di
                )
                /
                denominator
            )


        dx_values.append(
            dx
        )

        last_plus_di = plus_di
        last_minus_di = minus_di


    if len(dx_values) < period:

        return (
            0,
            last_plus_di,
            last_minus_di
        )


    adx_value = avg(
        dx_values[-period:]
    )


    return (
        adx_value,
        last_plus_di,
        last_minus_di
    )


# =========================================================
# UZUN VADELİ TREND CEZASI
# =========================================================

def long_term_penalty(
    d30,
    d90
):

    penalty = 0


    if d30 <= LT30_STRONG:

        penalty -= 8

    elif d30 <= LT30_MILD:

        penalty -= 4


    if d90 <= LT90_EXTREME:

        penalty -= 15

    elif d90 <= LT90_STRONG:

        penalty -= 10

    elif d90 <= LT90_MILD:

        penalty -= 5


    return penalty


# =========================================================
# UZUN VADELİ TREND SINIFI
# =========================================================

def long_term_status(
    d30,
    d90,
    available=True
):

    if not available:

        return (
            "VERİ YOK"
        )


    if (
        d90 <= LT90_EXTREME
        or
        d30 <= LT30_STRONG
    ):

        return (
            "YÜKSEK DÜŞÜŞ RİSKİ"
        )


    if (
        d90 <= LT90_STRONG
        or
        d30 <= LT30_MILD
    ):

        return (
            "DÜŞÜŞ RİSKİ"
        )


    if (
        d30 > 10
        and
        d90 > 0
    ):

        return (
            "POZİTİF TREND"
        )


    return (
        "NÖTR"
    )


# =========================================================
# TRADE COUNT GÜVENİ
#
# x[8] = number of trades.
#
# Bu benzersiz yatırımcı sayısı değildir.
# Sadece mum içindeki işlem sayısını gösterir.
# =========================================================

def trade_confidence(
    trade_count,
    volume_ratio
):

    if trade_count <= 0:

        return 0.0


    if trade_count >= (
        TRADE_COUNT_REFERENCE
    ):

        return 1.0


    base = (
        trade_count
        /
        TRADE_COUNT_REFERENCE
    )


    # Hacim çok yüksek olsa bile işlem sayısı
    # çok düşükse güveni sınırlıyoruz.

    if (
        volume_ratio >= 2.0
        and
        trade_count < MIN_1M_TRADES
    ):

        return 0.25


    if trade_count < MIN_1M_TRADES:

        return 0.40


    return min(
        1.0,
        max(
            0.40,
            base
        )
    )


# =========================================================
# TRADE COUNT DURUMU
# =========================================================

def trade_status(
    trades_1m,
    trades_5m
):

    if (
        trades_1m >= MIN_1M_TRADES
        and
        trades_5m >= MIN_5M_TRADES
    ):

        return (
            "YÜKSEK KATILIM"
        )


    if (
        trades_1m >= MIN_1M_TRADES
        or
        trades_5m >= MIN_5M_TRADES
    ):

        return (
            "ORTA KATILIM"
        )


    return (
        "DÜŞÜK KATILIM"
    )


# =========================================================
# BTC/TRY PİYASA REFERANSI
#
# Bu fonksiyon coin analizinden bağımsız çalışır.
# Cache kullanır.
# =========================================================

def market_context():

    now = time.time()


    with MARKET_CACHE_LOCK:

        cached = MARKET_CACHE.get(
            MARKET_SYMBOL
        )

        if cached:

            cached_time, cached_data = cached

            if (
                now - cached_time
                <
                LONG_TERM_CACHE_TTL
            ):

                return cached_data


    data = klines(
        MARKET_SYMBOL,
        "5m",
        20
    )


    if len(data) < 5:

        result = {
            "ok": False,
            "momentum": 0.0,
            "state": "VERİ YOK"
        }

        with MARKET_CACHE_LOCK:

            MARKET_CACHE[
                MARKET_SYMBOL
            ] = (
                now,
                result
            )

        return result


    try:

        closed = data[:-1]

        closes = [
            float(x[4])
            for x in closed
        ]

    except (
        TypeError,
        ValueError
    ):

        return {
            "ok": False,
            "momentum": 0.0,
            "state": "VERİ YOK"
        }


    bars_needed = max(
        1,
        MARKET_MOMENTUM_MINUTES // 5
    )


    if len(closes) <= bars_needed:

        return {
            "ok": False,
            "momentum": 0.0,
            "state": "VERİ YOK"
        }


    momentum = pct(
        closes[
            -1 - bars_needed
        ],
        closes[-1]
    )


    absolute = abs(
        momentum
    )


    if absolute >= (
        MARKET_EXTREME_MOVE
    ):

        state = (
            "AŞIRI HAREKETLİ"
        )

    elif absolute >= (
        MARKET_STRONG_MOVE
    ):

        state = (
            "HAREKETLİ"
        )

    elif momentum > 0.5:

        state = (
            "POZİTİF"
        )

    elif momentum < -0.5:

        state = (
            "NEGATİF"
        )

    else:

        state = (
            "NÖTR"
        )


    result = {
        "ok": True,
        "momentum": momentum,
        "state": state
    }


    with MARKET_CACHE_LOCK:

        MARKET_CACHE[
            MARKET_SYMBOL
        ] = (
            now,
            result
        )


    return result


# =========================================================
# MARKET CEZASI
#
# BTC'nin hareketli olması doğrudan AL sinyalini
# iptal etmiyor.
#
# Sadece coin hareketinin ne kadar "özel"
# olduğunu puanlamada kullanacağız.
# =========================================================

def market_penalty(
    market
):

    if not market.get(
        "ok",
        False
    ):

        return 0


    momentum = float(
        market.get(
            "momentum",
            0
        )
    )


    if abs(momentum) >= (
        MARKET_EXTREME_MOVE
    ):

        return -8


    if abs(momentum) >= (
        MARKET_STRONG_MOVE
    ):

        return -4


    return 0
# =========================================================
# DATABASE
# =========================================================

class DB:

    def __init__(self, path):

        self.path = path
        self.lock = Lock()

        with sqlite3.connect(
            self.path
        ) as db:

            # -------------------------------------------------
            # V22 | SQLite performans ayarları
            # -------------------------------------------------

            db.execute(
                "PRAGMA journal_mode=WAL"
            )

            db.execute(
                "PRAGMA synchronous=NORMAL"
            )

            db.execute(
                "PRAGMA busy_timeout=5000"
            )


            # -------------------------------------------------
            # SİNYAL DURUMU
            # -------------------------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL DEFAULT 0,
                    score REAL DEFAULT 0,
                    level TEXT DEFAULT 'NONE',
                    stage TEXT DEFAULT 'NONE',
                    updated REAL DEFAULT 0,

                    streak INTEGER DEFAULT 0,
                    streak_at REAL DEFAULT 0,
                    trap INTEGER DEFAULT 0,
                    priority REAL DEFAULT 0
                )
            """)


            # -------------------------------------------------
            # V21'den kalan state tablosu için migration
            #
            # Eğer eski DB kullanılıyorsa yeni kolonlar
            # otomatik olarak eklenir.
            # -------------------------------------------------

            existing_columns = {
                row[1]
                for row in db.execute(
                    "PRAGMA table_info(state)"
                ).fetchall()
            }


            migrations = {
                "streak":
                    "ALTER TABLE state ADD COLUMN "
                    "streak INTEGER DEFAULT 0",

                "streak_at":
                    "ALTER TABLE state ADD COLUMN "
                    "streak_at REAL DEFAULT 0",

                "trap":
                    "ALTER TABLE state ADD COLUMN "
                    "trap INTEGER DEFAULT 0",

                "priority":
                    "ALTER TABLE state ADD COLUMN "
                    "priority REAL DEFAULT 0"
            }


            for column, sql in migrations.items():

                if column not in existing_columns:

                    db.execute(sql)


            # -------------------------------------------------
            # SİNYAL SONUÇLARI
            # -------------------------------------------------

            db.execute("""
                CREATE TABLE IF NOT EXISTS signals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    symbol TEXT,
                    ts REAL,
                    price REAL,

                    score REAL,
                    setup REAL,
                    confirmation REAL,
                    penalty REAL,

                    status TEXT,

                    max_pct REAL DEFAULT 0,
                    min_pct REAL DEFAULT 0,

                    c1 REAL,
                    c3 REAL,
                    c5 REAL,
                    c15 REAL,

                    entry_quality REAL DEFAULT 0,
                    priority REAL DEFAULT 0,

                    d30 REAL DEFAULT 0,
                    d90 REAL DEFAULT 0,

                    trade_1m REAL DEFAULT 0,
                    trade_5m REAL DEFAULT 0,

                    market_momentum REAL DEFAULT 0,

                    trap INTEGER DEFAULT 0
                )
            """)


            # -------------------------------------------------
            # V21 signals tablosu migration
            # -------------------------------------------------

            existing_signal_columns = {
                row[1]
                for row in db.execute(
                    "PRAGMA table_info(signals)"
                ).fetchall()
            }


            signal_migrations = {

                "entry_quality":
                    "ALTER TABLE signals ADD COLUMN "
                    "entry_quality REAL DEFAULT 0",

                "priority":
                    "ALTER TABLE signals ADD COLUMN "
                    "priority REAL DEFAULT 0",

                "d30":
                    "ALTER TABLE signals ADD COLUMN "
                    "d30 REAL DEFAULT 0",

                "d90":
                    "ALTER TABLE signals ADD COLUMN "
                    "d90 REAL DEFAULT 0",

                "trade_1m":
                    "ALTER TABLE signals ADD COLUMN "
                    "trade_1m REAL DEFAULT 0",

                "trade_5m":
                    "ALTER TABLE signals ADD COLUMN "
                    "trade_5m REAL DEFAULT 0",

                "market_momentum":
                    "ALTER TABLE signals ADD COLUMN "
                    "market_momentum REAL DEFAULT 0",

                "trap":
                    "ALTER TABLE signals ADD COLUMN "
                    "trap INTEGER DEFAULT 0"
            }


            for column, sql in (
                signal_migrations.items()
            ):

                if column not in (
                    existing_signal_columns
                ):

                    db.execute(sql)


    # =====================================================
    # STATE OKUMA
    # =====================================================

    def get(self, symbol):

        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):

            db.execute(
                "PRAGMA busy_timeout=5000"
            )

            return db.execute(
                """
                SELECT
                    sent,
                    score,
                    level,
                    stage,
                    updated,
                    streak,
                    streak_at,
                    trap,
                    priority
                FROM state
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()


    # =====================================================
    # STATE YAZMA
    # =====================================================

    def put(
        self,
        symbol,
        score,
        level,
        stage,
        sent=None,
        streak=None,
        trap=None,
        priority=None
    ):

        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):

            db.execute(
                "PRAGMA busy_timeout=5000"
            )


            old = db.execute(
                """
                SELECT
                    sent,
                    streak,
                    trap,
                    priority
                FROM state
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()


            sent_time = (
                time.time()
                if sent is not None
                else (
                    old[0]
                    if old
                    else 0
                )
            )


            old_streak = (
                old[1]
                if old
                else 0
            )

            old_trap = (
                old[2]
                if old
                else 0
            )

            old_priority = (
                old[3]
                if old
                else 0
            )


            final_streak = (
                streak
                if streak is not None
                else old_streak
            )

            final_trap = (
                int(trap)
                if trap is not None
                else int(old_trap)
            )

            final_priority = (
                priority
                if priority is not None
                else old_priority
            )


            db.execute(
                """
                INSERT INTO state(
                    symbol,
                    sent,
                    score,
                    level,
                    stage,
                    updated,
                    streak,
                    streak_at,
                    trap,
                    priority
                )
                VALUES(
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )

                ON CONFLICT(symbol)
                DO UPDATE SET
                    sent=excluded.sent,
                    score=excluded.score,
                    level=excluded.level,
                    stage=excluded.stage,
                    updated=excluded.updated,
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    priority=excluded.priority
                """,
                (
                    symbol,
                    sent_time,
                    score,
                    level,
                    stage,
                    time.time(),
                    final_streak,
                    time.time(),
                    final_trap,
                    final_priority
                )
            )


    # =====================================================
    # STREAK GÜNCELLEME
    #
    # Aynı aday art arda güçlü görünüyorsa streak artar.
    #
    # Aradaki süre STREAK_WINDOW'u aşarsa streak sıfırlanır.
    # =====================================================

    def update_streak(
        self,
        symbol,
        qualified,
        trap=False
    ):

        now = time.time()


        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):

            db.execute(
                "PRAGMA busy_timeout=5000"
            )


            row = db.execute(
                """
                SELECT
                    streak,
                    streak_at
                FROM state
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()


            if row:

                old_streak = int(
                    row[0] or 0
                )

                old_time = float(
                    row[1] or 0
                )

            else:

                old_streak = 0
                old_time = 0


            if not qualified:

                new_streak = 0

            elif (
                old_time > 0
                and
                now - old_time
                <=
                STREAK_WINDOW
            ):

                new_streak = (
                    old_streak + 1
                )

            else:

                new_streak = 1


            db.execute(
                """
                INSERT INTO state(
                    symbol,
                    streak,
                    streak_at,
                    trap,
                    updated
                )
                VALUES(
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )

                ON CONFLICT(symbol)
                DO UPDATE SET
                    streak=excluded.streak,
                    streak_at=excluded.streak_at,
                    trap=excluded.trap,
                    updated=excluded.updated
                """,
                (
                    symbol,
                    new_streak,
                    now,
                    int(trap),
                    now
                )
            )


            return new_streak


    # =====================================================
    # CAN SEND
    # =====================================================

    def can_send(
        self,
        symbol,
        level
    ):

        row = self.get(
            symbol
        )

        if not row:

            return True


        previous_sent = float(
            row[0] or 0
        )

        previous_level = row[2]


        rank = {
            "BUY": 1,
            "VERY": 2
        }


        old_rank = rank.get(
            previous_level,
            0
        )

        new_rank = rank.get(
            level,
            0
        )


        return (
            time.time()
            -
            previous_sent
            >=
            COOLDOWN
        ) or (
            new_rank > old_rank
        )


    # =====================================================
    # SIGNAL KAYDI
    # =====================================================

    def create_signal(
        self,
        result
    ):

        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):

            db.execute(
                "PRAGMA busy_timeout=5000"
            )


            cur = db.execute(
                """
                INSERT INTO signals(
                    symbol,
                    ts,
                    price,
                    score,
                    setup,
                    confirmation,
                    penalty,
                    status,

                    max_pct,
                    min_pct,

                    entry_quality,
                    priority,

                    d30,
                    d90,

                    trade_1m,
                    trade_5m,

                    market_momentum,

                    trap
                )
                VALUES(
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    0,
                    0,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    result["symbol"],
                    time.time(),
                    result["price"],
                    result["score"],
                    result["setup"],
                    result["confirmation"],
                    result["penalty"],
                    result["status"],

                    result.get(
                        "entry_quality",
                        0
                    ),

                    result.get(
                        "priority",
                        0
                    ),

                    result.get(
                        "d30",
                        0
                    ),

                    result.get(
                        "d90",
                        0
                    ),

                    result.get(
                        "trades_1m",
                        0
                    ),

                    result.get(
                        "trades_5m",
                        0
                    ),

                    result.get(
                        "market_momentum",
                        0
                    ),

                    int(
                        result.get(
                            "trap",
                            False
                        )
                    )
                )
            )


            return cur.lastrowid


    # =====================================================
    # OUTCOME GÜNCELLEME
    # =====================================================

    def update_outcomes(
        self,
        price_map
    ):

        now = time.time()


        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):

            db.execute(
                "PRAGMA busy_timeout=5000"
            )


            rows = db.execute(
                """
                SELECT
                    id,
                    symbol,
                    ts,
                    price,
                    max_pct,
                    min_pct,
                    c1,
                    c3,
                    c5,
                    c15
                FROM signals
                WHERE ts > ?
                """,
                (
                    now - OUTCOME_WINDOW,
                )
            ).fetchall()


            for (
                id_,
                symbol,
                ts,
                price,
                max_pct,
                min_pct,
                c1,
                c3,
                c5,
                c15
            ) in rows:


                cur_price = price_map.get(
                    symbol
                )


                if (
                    not cur_price
                    or
                    not price
                    or
                    price <= 0
                ):

                    continue


                change = (
                    (
                        cur_price
                        -
                        price
                    )
                    /
                    price
                    *
                    100
                )


                new_max = max(
                    max_pct,
                    change
                )

                new_min = min(
                    min_pct,
                    change
                )


                elapsed = (
                    now - ts
                )


                updates = {
                    "max_pct": new_max,
                    "min_pct": new_min
                }


                if (
                    elapsed >= 60
                    and
                    c1 is None
                ):

                    updates[
                        "c1"
                    ] = change


                if (
                    elapsed >= 180
                    and
                    c3 is None
                ):

                    updates[
                        "c3"
                    ] = change


                if (
                    elapsed >= 300
                    and
                    c5 is None
                ):

                    updates[
                        "c5"
                    ] = change


                if (
                    elapsed >= 900
                    and
                    c15 is None
                ):

                    updates[
                        "c15"
                    ] = change


                set_clause = ", ".join(
                    f"{key}=?"
                    for key in updates
                )


                db.execute(
                    f"""
                    UPDATE signals
                    SET {set_clause}
                    WHERE id=?
                    """,
                    (
                        *updates.values(),
                        id_
                    )
                )


    # =====================================================
    # GELİŞMİŞ PERFORMANS VERİSİ
    # =====================================================

    def performance_summary(
        self
    ):

        with (
            self.lock,
            sqlite3.connect(
                self.path,
                timeout=5
            ) as db
        ):

            db.execute(
                "PRAGMA busy_timeout=5000"
            )


            return db.execute(
                """
                SELECT
                    score,
                    setup,
                  
