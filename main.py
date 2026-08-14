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
           confirmation,
                    max_pct,
                    min_pct,
                    c5,
                    c15,
                    status,
                    entry_quality,
                    priority,
                    d30,
                    d90,
                    trade_1m,
                    trade_5m,
                    market_momentum,
                    trap
                FROM signals
                WHERE c15 IS NOT NULL
                """
            ).fetchall()


DBS = DB(
    DB_PATH
)       
# =========================================================
# ADAYLAR
# =========================================================

def candidates(data):

    result = []


    for ticker in data:

        symbol = ticker.get(
            "symbol",
            ""
        )


        if not symbol.endswith(
            "TRY"
        ):
continue


        if symbol in EXCLUDED:

            continue


        try:

            quote_volume = float(
                ticker.get(
                    "quoteVolume",
                    0
                )
            )


            change = float(
                ticker.get(
                    "priceChangePercent",
                    0
                )
            )


            price = float(
                ticker.get(
"lastPrice",
                    0
                )
            )


            if (
                quote_volume
                <
                MIN_QUOTE_VOLUME
            ):

                continue


            # Aşırı günlük yükselişi kovalamıyoruz.
if change > 25:

                continue


            result.append({
                "symbol": symbol,
                "volume": quote_volume,
                "chg": change,
                "price": price
            })


        except (
            TypeError,
            ValueError
        ):

            continue


    return result
# =========================================================
# SHORTLIST
# =========================================================

def shortlist(items):

    def ranking(item):

        volume = item[
            "volume"
        ]

        positive_change = max(
            item["chg"],
            0
        )

 return (
            volume
            *
            (
                1
                +
                positive_change
                /
                100
            )
        )


    return sorted(
        items,
        key=ranking,
        reverse=True
    )[
        :SHORTLIST
    ]
# =========================================================
# V22 | ANALİZ MOTORU
# =========================================================

def analyze(item):

    symbol = item["symbol"]
    price = item["price"]

    try:

        # =================================================
        # 1) 5M ÖN FİLTRE
        #
        # ÖNEMLİ:
        # daily_trend() bundan önce çağrılmıyor.
        # Böylece ölü/zayıf adaylarda gereksiz 1d
        # API isteği yapılmıyor.
        # =================================================

        k5 = klines(
            symbol,
            "5m",
            80
        )

        if len(k5) < 40:

            return {
                "status": "PASS"
            }


        closed_5m = k5[:-1]


        close5 = [
            float(x[4])
            for x in closed_5m
        ]


        volume5 = [
            float(x[7])
            for x in closed_5m
        ]


        trades5 = [
            int(x[8])
            for x in closed_5m
        ]


        average_5m_volume = avg(
            volume5[-12:]
        )


        recent_5m_volume = avg(
            volume5[-3:]
        )


        volume5_ratio = (
            recent_5m_volume
            /
            average_5m_volume
            if average_5m_volume
            else 0
        )


        momentum_15m = pct(
            close5[-4],
            price
        )


        # Ölü düşüş:
        # Fiyat sert düşüyor ve hacim bunu
        # desteklemiyorsa daha ileri analize
        # gerek yok.

        if (
            momentum_15m < -3
            and
            volume5_ratio < 1.3
        ):

            return {
                "status": "PASS"
            }


        # =================================================
        # 2) UZUN VADELİ TREND
        #
        # Artık 5m gate geçildikten sonra çağrılıyor.
        # =================================================

        trend = daily_trend(
            symbol
        )


        long_term_ok = trend.get(
            "ok",
            False
        )


        d30 = trend.get(
            "d30",
            0.0
        )


        d90 = trend.get(
            "d90",
            0.0
        )


        trend_penalty = (
            long_term_penalty(
                d30,
                d90
            )
            if long_term_ok
            else 0
        )


        trend_state = long_term_status(
            d30,
            d90,
            long_term_ok
        )


        # =================================================
        # 3) 1M VERİ
        # =================================================

        k1 = klines(
            symbol,
            "1m",
            180
        )


        if len(k1) < 100:

            return {
                "status": "PASS"
            }


        closed_1m = k1[:-1]


        close = [
            float(x[4])
            for x in closed_1m
        ]


        high = [
            float(x[2])
            for x in closed_1m
        ]


        low = [
            float(x[3])
            for x in closed_1m
        ]


        open_price = [
            float(x[1])
            for x in closed_1m
        ]


        volume = [
            float(x[7])
            for x in closed_1m
        ]


        trades = [
            int(x[8])
            for x in closed_1m
        ]


        if price <= 0:

            price = close[-1]


        # =================================================
        # 4) MOMENTUM
        # =================================================

        momentum_1m = pct(
            close[-2],
            price
        )


        momentum_5m = pct(
            close5[-2],
            price
        )


        # =================================================
        # 5) 90 MUM FİYAT KONUMU
        # =================================================

        low_90 = min(
            low[-90:]
        )


        high_90 = max(
            high[-90:]
        )


        location = (
            (
                price
                -
                low_90
            )
            /
            (
                high_90
                -
                low_90
            )
            *
            100
            if high_90 > low_90
            else 50
        )


        # =================================================
        # 6) HACİM
        # =================================================

        average_volume = avg(
            volume[-30:]
        )


        last_3_volume = avg(
            volume[-3:]
        )


        previous_volume = avg(
            volume[-10:-3]
        )


        volume_ratio = (
            last_3_volume
            /
            average_volume
            if average_volume
            else 0
        )


        volume_impulse = (
            last_3_volume
            /
            previous_volume
            if previous_volume
            else 1
        )


        # =================================================
        # 7) ALICI HACMİ
        # =================================================

        buy_volume = sum(
            float(x[10])
            for x in closed_1m[-5:]
        )


        total_volume = sum(
            float(x[7])
            for x in closed_1m[-5:]
        )


        buyer_percent = (
            buy_volume
            /
            total_volume
            *
            100
            if total_volume
            else 50
        )


        # =================================================
        # 8) TRADE COUNT
        #
        # Son 5 dakikadaki toplam işlem sayısını
        # ayrıca hesaplıyoruz.
        # =================================================

        trades_1m = sum(
            trades[-5:]
        )


        trades_5m = sum(
            trades5[-1:]
        )


        trade_status_text = trade_status(
            trades_1m,
            trades_5m
        )


        trade_conf = trade_confidence(
            trades_1m,
            volume_ratio
        )


        # =================================================
        # 9) EMA
        # =================================================

        ema9 = ema(
            close,
            9
        )


        ema21 = ema(
            close,
            21
        )


        ema50 = ema(
            close,
            50
        )


        ema9_previous = ema(
            close[:-3],
            9
        )


        ema21_previous = ema(
            close[:-3],
            21
        )


        ema_up = (
            ema9 > ema21
            and
            ema9 > ema9_previous
        )


        ema_cross = (
            ema9 > ema21
            and
            ema9_previous
            <=
            ema21_previous
        )


        # =================================================
        # 10) RSI
        # =================================================

        current_rsi = rsi(
            close
        )


        previous_rsi = rsi(
            close[:-3]
        )


        # =================================================
        # 11) MACD
        # =================================================

        _, _, macd_histogram = macd(
            close
        )


        _, _, previous_macd_histogram = macd(
            close[:-3]
        )


        macd_up = (
            macd_histogram
            >
            previous_macd_histogram
        )


        # =================================================
        # 12) ADX
        # =================================================

        adx_value, plus_di, minus_di = adx(
            high,
            low,
            close
        )


        # =================================================
        # 13) BOLLINGER
        # =================================================

        lower, middle, upper = bb(
            close
        )


        width = (
            (
                upper
                -
                lower
            )
            /
            middle
            *
            100
            if middle
            else 0
        )


        old_lower, old_middle, old_upper = bb(
            close[:-5]
        )


        old_width = (
            (
                old_upper
                -
                old_lower
            )
            /
            old_middle
            *
            100
            if old_middle
            else width
        )


        squeeze = (
            width <= 2.2
            or
            (
                old_width > 0
                and
                width
                <
                old_width * 0.80
            )
        )


        expanding = (
            old_width > 0
            and
            width
            >
            old_width * 1.08
        )


        # =================================================
        # 14) DİRENÇ / KIRILIM
        # =================================================

        resistance = max(
            high[-30:-2]
        )


        distance_to_resistance = max(
            0,
            (
                resistance
                -
                price
            )
            /
            price
            *
            100
        )


        breakout = (
            price
            >
            resistance
        )


        closed_breakout = (
            close[-1]
            >
            resistance
        )


        near_resistance = (
            distance_to_resistance
            <=
            0.35
        )


        # =================================================
        # 15) SON MUM
        # =================================================

        candle_range = (
            high[-1]
            -
            low[-1]
        )


        close_position = (
            (
                close[-1]
                -
                low[-1]
            )
            /
            candle_range
            *
            100
            if candle_range > 0
            else 50
        )


        higher_low = (
            low[-1] > low[-3]
            and
            low[-3] >= low[-6]
        )


        # =================================================
        # 16) TRAP TESPİTİ
        # =================================================

        trap_reasons = []


        if (
            buyer_percent
            <
            TRAP_BUYER_LOW
            and
            volume_ratio
            >=
            TRAP_VOLUME_HIGH
        ):

            trap_reasons.append(
                "zayıf alıcı"
            )


        if (
            momentum_5m
            <
            TRAP_MOMENTUM_NEGATIVE
            and
            not higher_low
        ):

            trap_reasons.append(
                "negatif momentum"
            )


        if (
            trades_1m
            <
            MIN_1M_TRADES
            and
            volume_ratio
            >=
            2.0
        ):

            trap_reasons.append(
                "düşük işlem katılımı"
            )


        trap = bool(
            trap_reasons
        )


        # =================================================
        # 17) SETUP
        # =================================================

        setup = 0


        if ema_up:

            setup += 12


        if ema_cross:

            setup += 6


        if squeeze:

            setup += 8


        if higher_low:

            setup += 6


        if (
            35
            <=
            current_rsi
            <=
            65
            and
            current_rsi
            >
            previous_rsi
        ):

            setup += 8


        if price >= ema50:

            setup += 5


        if (
            near_resistance
            or
            distance_to_resistance
            <=
            0.70
        ):

            setup += 8


        if volume_ratio >= 1.5:

            setup += 8


        if buyer_percent >= 58:

            setup += 5


        # İşlem katılımı yeterliyse setup'a
        # küçük bir güven katkısı.

        if (
            trades_1m
            >=
            MIN_1M_TRADES
        ):

            setup += 3


        # =================================================
        # 18) CONFIRMATION
        # =================================================

        confirmation = 0


        if closed_breakout:

            confirmation += 18

        elif breakout:

            confirmation += 14


        if volume_ratio >= 2.0:

            confirmation += 12

        elif volume_ratio >= 1.5:

            confirmation += 7


        if volume5_ratio >= 1.5:

            confirmation += 8


        if buyer_percent >= 65:

            confirmation += 7


        if macd_up:

            confirmation += 6


        if (
            plus_di
            >
            minus_di
            and
            adx_value
            >=
            18
        ):

            confirmation += 7


        if close_position >= 65:

            confirmation += 4


        if expanding:

            confirmation += 4


        # Trade count yeterliyse teyidin
        # güvenilirliğini artırıyoruz.

        if (
            trades_1m
            >=
            MIN_1M_TRADES
            and
            trades_5m
            >=
            MIN_5M_TRADES
        ):

            confirmation += 3


        # =================================================
        # 19) KISA VADELİ PENALTY
        # =================================================

        penalty = 0


        if momentum_1m > 2.5:

            penalty -= 10


        if momentum_5m > 5:

            penalty -= 12


        if current_rsi > 78:

            penalty -= 10


        if (
            buyer_percent < 50
            and
            volume_ratio >= 1.8
        ):

            penalty -= 8


        if (
            momentum_5m < -1.2
            and
            not higher_low
        ):

            penalty -= 12


        # =================================================
        # 20) UZUN VADELİ PENALTY
        # =================================================

        penalty += (
            trend_penalty
        )


        # =================================================
        # 21) TRADE COUNT GÜVEN CEZASI
        #
        # Çok yüksek hacim + çok düşük işlem sayısı
        # güveni azaltır.
        # =================================================

        if (
            volume_ratio >= 2.0
            and
            trades_1m
            <
            MIN_1M_TRADES
        ):

            penalty -= 8


        elif (
            volume_ratio >= 1.5
            and
            trades_1m
            <
            MIN_1M_TRADES
        ):

            penalty -= 4


        # =================================================
        # 22) TRAP PENALTY
        # =================================================

        if trap:

            penalty -= 12


        # =================================================
        # 23) BTC/TRY BAĞLAMI
        # =================================================

        market = market_context()


        market_momentum = float(
            market.get(
                "momentum",
                0
            )
        )


        market_state = market.get(
            "state",
            "VERİ YOK"
        )


        penalty += market_penalty(
            market
        )


        # =================================================
        # 24) GİRİŞ KALİTESİ
        #
        # 100 = erken / dengeli
        # 0   = aşırı kovalanmış
        # =================================================

        entry_quality = 100.0


        # RSI aşırılığı

        if (
            current_rsi
            >=
            ENTRY_RSI_EXTREME
        ):

            entry_quality -= 30

        elif (
            current_rsi
            >=
            ENTRY_RSI_HIGH
        ):

            entry_quality -= 15


        # Çok hızlı 1m hareket

        if (
            momentum_1m
            >=
            ENTRY_MOMENTUM_EXTREME
        ):

            entry_quality -= 25

        elif (
            momentum_1m
            >=
            ENTRY_MOMENTUM_HIGH
        ):

            entry_quality -= 12


        # Çok hızlı 5m hareket

        if momentum_5m >= 5:

            entry_quality -= 20

        elif momentum_5m >= 3:

            entry_quality -= 10


        # Dirence çok yakınsa risk artıyor.

        if distance_to_resistance <= 0.15:

            entry_quality -= 8

        elif distance_to_resistance <= 0.35:

            entry_quality -= 4


        # Yeni kırılımın kapanış teyidi varsa
        # giriş kalitesi biraz yükseliyor.

        if closed_breakout:

            entry_quality += 5


        # Higher-Low erken yapı için olumlu.

        if higher_low:

            entry_quality += 5


        # Yeterli işlem katılımı olumlu.

        if (
            trades_1m
            >=
            MIN_1M_TRADES
        ):

            entry_quality += 4

        else:

            entry_quality -= 8


        # Trap doğrudan giriş kalitesini de düşürür.

        if trap:

            entry_quality -= 20


        entry_quality = max(
            0,
            min(
                100,
                int(
                    round(
                        entry_quality
                    )
                )
            )
        )


        # =================================================
        # 25) ANA SKOR
        # =================================================

        score = clamp(
            setup
            +
            confirmation
            +
            penalty
        )


        # =================================================
        # 26) STAGE
        # =================================================

        stage = "NONE"


        if setup >= 25:

            stage = "SETUP"


        if (
            score >= 68
            and
            confirmation >= 18
        ):

            stage = "CONFIRMED"


        # VERY için uzun vadeli sert düşüş filtresi.

        very_long_term_ok = (
            not long_term_ok
            or
            (
                d30
                >
                LT30_STRONG
                and
                d90
                >
                LT90_STRONG
            )
        )


        # Trap olan sinyal doğrudan VERY olamaz.

        very_trap_ok = (
            not trap
        )


        # Çok düşük işlem katılımıyla gelen
        # aşırı hacim patlaması VERY olamaz.

        very_trade_ok = (
            trades_1m
            >=
            MIN_1M_TRADES
        )


        if (
            score >= 84
            and
            confirmation >= 28
            and
            volume_ratio >= 1.5
            and
            very_long_term_ok
            and
            very_trap_ok
            and
            very_trade_ok
        ):

            stage = "VERY"


        # =================================================
        # 27) LEVEL
        # =================================================

        if stage == "CONFIRMED":

            level = "BUY"

        elif stage == "VERY":

            level = "VERY"

        elif stage == "SETUP":

            level = "INTERNAL"

        else:

            level = "PASS"


        # =================================================
        # 28) STREAK
        #
        # Burada streak'i sadece gerçekten
        # anlamlı SETUP/CONFIRM adaylarında artırıyoruz.
        #
        # PASS veya TRAP adayları streak biriktiremez.
        # =================================================

        streak_qualified = (
            stage in (
                "SETUP",
                "CONFIRMED",
                "VERY"
            )
            and
            not trap
        )


        streak = DBS.update_streak(
            symbol,
            streak_qualified,
            trap=trap
        )


        # ==========================
===================
        # 29) STREAK GEREKSİNİMİ
        # =================================================

        if level == "BUY":

            if (
                streak
                <
                BUY_STREAK_REQUIRED
            ):
 # Tek taramada oluşan güçlü
                # hareketi hemen dışarı göndermiyoruz.
                #
                # Ancak state'te SETUP olarak
                # tutulmaya devam ediyor.

                level = "INTERNAL"


        elif level == "VERY":

            if (
                streak
                <
                VERY_STREAK_REQUIRED
            ):
# Çok güçlü anlık breakout için
                # kontrollü istisna:
                #
                # Kapalı mum kırılımı + yüksek hacim
                # + güçlü alıcı + yeterli işlem
                # varsa streak beklemeden korunabilir.

                instant_very = (
                    closed_breakout
                    and
                    volume_ratio >= 2.0
                    and
