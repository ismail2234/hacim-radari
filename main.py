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
