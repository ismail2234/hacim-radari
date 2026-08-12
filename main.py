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
# BALİNA RADARI V21
# Binance TR / TRY odaklı
# ============================================================

BASE = os.getenv(
    "BINANCE_TR_BASE",
    "https://api.binance.me"
)

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "30"))
WORKERS = int(os.getenv("MAX_WORKERS", "10"))

MAX_SIGNALS = int(
    os.getenv("MAX_SIGNALS_PER_SCAN", "2")
)

COOLDOWN = int(
    os.getenv("SIGNAL_COOLDOWN", "1200")
)

MIN_QUOTE_VOLUME = float(
    os.getenv("MIN_QUOTE_VOLUME_TRY", "1000000")
)

SHORTLIST = int(
    os.getenv("SHORTLIST_SIZE", "80")
)

TIMEOUT = int(
    os.getenv("REQUEST_TIMEOUT", "8")
)

DB_PATH = os.getenv(
    "STATE_DB_PATH",
    "balina_v21.db"
)

TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

CHAT = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


EXCLUDED = {
    "USDTTRY",
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "BUSDUSDT",
    "DAIUSDT",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout
)

log = logging.getLogger("balina-v21")


# ============================================================
# HTTP SESSION
# ============================================================

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
            allowed_methods=["GET", "POST"],
            **retry_args
        )
    except TypeError:
        retry = Retry(
            method_whitelist=["GET", "POST"],
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
        "User-Agent": "BalinaRadari-V21/1.0"
    })

    return session


S = build_session()


# ============================================================
# API
# ============================================================

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


def telegram(text):

    if not TOKEN or not CHAT:
        return False

    try:

        response = S.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT,
                "text": text
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        return bool(
            response.json().get("ok")
        )

    except Exception as e:

        log.error(
            "Telegram: %s",
            e
        )

        return False


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


# ============================================================
# MATEMATİK
# ============================================================

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
            int(round(value))
        )
    )


def ema(values, period):

    if not values:
        return 0.0

    if len(values) < period:
        return avg(values)

    k = 2 / (period + 1)

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


def rsi(values, period=14):

    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):

        diff = (
            values[i]
            -
            values[i - 1]
        )

        gains.append(
            max(diff, 0)
        )

        losses.append(
            max(-diff, 0)
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
            average_gain / average_loss
        )
    )


def macd(values):

    if len(values) < 35:
        return 0, 0, 0

    macd_values = []

    for i in range(
        26,
        len(values) + 1
    ):

        macd_values.append(
            ema(values[:i], 12)
            -
            ema(values[:i], 26)
        )

    main = macd_values[-1]

    signal = ema(
        macd_values,
        9
    )

    histogram = (
        main - signal
    )

    return (
        main,
        signal,
        histogram
    )


def bb(values, period=20, k=2):

    if len(values) < period:
        return 0, 0, 0

    sample = values[-period:]

    middle = avg(sample)

    deviation = (
        avg([
            (x - middle) ** 2
            for x in sample
        ])
    ) ** 0.5

    return (
        middle - k * deviation,
        middle,
        middle + k * deviation
    )


def adx(highs, lows, closes, period=14):

    if len(closes) < period * 2 + 1:
        return 0, 0, 0

    true_ranges = []
    plus_moves = []
    minus_moves = []

    for i in range(
        1,
        len(closes)
    ):

        true_ranges.append(
            max(
                highs[i] - lows[i],
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
        )

        up = (
            highs[i]
            -
            highs[i - 1]
        )

        down = (
            lows[i - 1]
            -
            lows[i]
        )

        plus_moves.append(
            up
            if up > down and up > 0
            else 0
        )

        minus_moves.append(
            down
            if down > up and down > 0
            else 0
        )

    atr = avg(
        true_ranges[-period:]
    )

    plus = avg(
        plus_moves[-period:]
    )

    minus = avg(
        minus_moves[-period:]
    )

    if atr <= 0:
        return 0, 0, 0

    plus_di = (
        100 * plus / atr
    )

    minus_di = (
        100 * minus / atr
    )

    dx = (
        100
        *
        abs(plus_di - minus_di)
        /
        (plus_di + minus_di)
        if plus_di + minus_di
        else 0
    )

    return (
        dx,
        plus_di,
        minus_di
)
# ============================================================
# DATABASE
# ============================================================

class DB:

    def __init__(self, path):

        self.path = path
        self.lock = Lock()

        with sqlite3.connect(path) as db:

            db.execute("""
                CREATE TABLE IF NOT EXISTS state(
                    symbol TEXT PRIMARY KEY,
                    sent REAL DEFAULT 0,
                    score REAL DEFAULT 0,
                    level TEXT DEFAULT 'NONE',
                    stage TEXT DEFAULT 'NONE',
                    updated REAL DEFAULT 0
                )
            """)


    def get(self, symbol):

        with (
            self.lock,
            sqlite3.connect(self.path) as db
        ):

            return db.execute(
                """
                SELECT
                    sent,
                    score,
                    level,
                    stage,
                    updated
                FROM state
                WHERE symbol=?
                """,
                (symbol,)
            ).fetchone()


    def put(
        self,
        symbol,
        score,
        level,
        stage,
        sent=None
    ):

        with (
            self.lock,
            sqlite3.connect(self.path) as db
        ):

            old = db.execute(
                """
                SELECT sent
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

            db.execute(
                """
                INSERT INTO state(
                    symbol,
                    sent,
                    score,
                    level,
                    stage,
                    updated
                )
                VALUES(
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
                    updated=excluded.updated
                """,
                (
                    symbol,
                    sent_time,
                    score,
                    level,
                    stage,
                    time.time()
                )
            )


    def can_send(
        self,
        symbol,
        level
    ):

        row = self.get(symbol)

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


DBS = DB(DB_PATH)


# ============================================================
# ADAYLAR
# ============================================================

def candidates(data):

    result = []

    for ticker in data:

        symbol = ticker.get(
            "symbol",
            ""
        )

        # SADECE Binance TR TRY marketleri
        if not symbol.endswith("TRY"):
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

            if quote_volume < MIN_QUOTE_VOLUME:
                continue

            # Zaten aşırı kaçmış coinleri
            # doğrudan AL olarak kovalamıyoruz.
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


def shortlist(items):

    def ranking(item):

        volume = item["volume"]

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
                positive_change / 100
            )
        )

    return sorted(
        items,
        key=ranking,
        reverse=True
    )[:SHORTLIST]


# ============================================================
# ANA ANALİZ
# ============================================================

def analyze(item):

    symbol = item["symbol"]
    price = item["price"]

    try:

        # Önce 5 dakika.
        # Böylece her coin için aynı anda
        # 1m + 5m yükü oluşturmuyoruz.

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

        # Tamamen ölü coinleri
        # 1m derin taramaya sokma.
        if (
            momentum_15m < -3
            and
            volume5_ratio < 1.3
        ):
            return {
                "status": "PASS"
            }


        # ----------------------------------------------------
        # 1 DAKİKALIK DERİN ANALİZ
        # ----------------------------------------------------

        k1 = klines(
            symbol,
            "1m",
            180
        )

        if len(k1) < 100:
            return {
                "status": "PASS"
            }

        # Açık mum kullanılmıyor.
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

        if price <= 0:
            price = close[-1]


        # ----------------------------------------------------
        # FİYAT İVMESİ
        # ----------------------------------------------------

        momentum_1m = pct(
            close[-2],
            price
        )

        momentum_5m = pct(
            close5[-2],
            price
        )

        low_90 = min(
            low[-90:]
        )

        high_90 = max(
            high[-90:]
        )

        location = (
            (
                price - low_90
            )
            /
            (
                high_90 - low_90
            )
            *
            100
            if high_90 > low_90
            else 50
        )


        # ----------------------------------------------------
        # HACİM İVMESİ
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # ALICI BASKISI
        # Binance kline:
        # x[10] = taker buy quote volume
        # x[7]  = total quote volume
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # EMA
        # ----------------------------------------------------

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
            ema9_previous <= ema21_previous
        )


        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        current_rsi = rsi(
            close
        )

        previous_rsi = rsi(
            close[:-3]
        )


        # ----------------------------------------------------
        # MACD
        # ----------------------------------------------------

        _, _, macd_histogram = macd(
            close
        )

        _, _, previous_macd_histogram = macd(
            close[:-3]
        )


        # ----------------------------------------------------
        # ADX
        # ----------------------------------------------------

        adx_value, plus_di, minus_di = adx(
            high,
            low,
            close
        )


        # ----------------------------------------------------
        # BOLLINGER
        # ----------------------------------------------------

        lower, middle, upper = bb(
            close
        )

        width = (
            (upper - lower)
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
            (old_upper - old_lower)
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
                width < old_width * 0.80
            )
        )

        expanding = (
            old_width > 0
            and
            width > old_width * 1.08
            )
        # ----------------------------------------------------
        # DİRENÇ / KIRILIM
        # ----------------------------------------------------

        resistance = max(
            high[-30:-2]
        )

        distance_to_resistance = max(
            0,
            (
                resistance - price
            )
            /
            price
            *
            100
        )

        breakout = (
            price > resistance
        )

        closed_breakout = (
            close[-1] > resistance
        )

        near_resistance = (
            distance_to_resistance <= 0.35
        )


        # Son mumun kapanış gücü.
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


        # ----------------------------------------------------
        # HIGHER LOW
        # ----------------------------------------------------

        higher_low = (
            low[-1] > low[-3]
            and
            low[-3] >= low[-6]
        )


        # ====================================================
        # İÇ SİSTEM 1
        #
        # SETUP
        #
        # BUNUN TELEGRAM MESAJI YOK.
        # ====================================================

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
            35 <= current_rsi <= 65
            and
            current_rsi > previous_rsi
        ):
            setup += 8

        if price >= ema50:
            setup += 5

        if (
            near_resistance
            or
            distance_to_resistance <= 0.70
        ):
            setup += 8

        if volume_ratio >= 1.5:
            setup += 8

        if buyer_percent >= 58:
            setup += 5


        # ====================================================
        # İÇ SİSTEM 2
        #
        # CONFIRMATION
        #
        # BURADA ARTIK HAREKET GERÇEKLEŞİYOR MU
        # DİYE KONTROL EDİYORUZ.
        # ====================================================

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


        if (
            macd_histogram
            >
            previous_macd_histogram
        ):
            confirmation += 6


        if (
            plus_di > minus_di
            and
            adx_value >= 18
        ):
            confirmation += 7


        if close_position >= 65:
            confirmation += 4


        if expanding:
            confirmation += 4


        # ====================================================
        # RİSK / AŞIRI KAÇMA CEZASI
        # ====================================================

        penalty = 0


        # Son dakikada zaten patlamışsa
        # körlemesine kovalamıyoruz.

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


        # ====================================================
        # TOPLAM SKOR
        # ====================================================

        score = clamp(
            setup
            +
            confirmation
            +
            penalty
        )


        # ====================================================
        # İÇ AŞAMALAR
        # ====================================================

        stage = "NONE"


        if setup >= 25:
            stage = "SETUP"


        if (
            score >= 68
            and
            confirmation >= 18
        ):
            stage = "CONFIRMED"


        if (
            score >= 84
            and
            confirmation >= 28
            and
            volume_ratio >= 1.5
        ):
            stage = "VERY"


        # ====================================================
        # TELEGRAM SEVİYELERİ
        #
        # SETUP     -> MESAJ YOK
        # CONFIRMED -> 🟢 AL
        # VERY      -> 🔥 ÇOK GÜÇLÜ AL
        # ====================================================

        if stage == "CONFIRMED":

            level = "BUY"

        elif stage == "VERY":

            level = "VERY"

        elif stage == "SETUP":

            level = "INTERNAL"

        else:

            level = "PASS"


        # İç durumu DB'ye yaz.
        DBS.put(
            symbol,
            score,
            level,
            stage
        )


        # TAKİP MESAJI YOK.
        if level not in (
            "BUY",
            "VERY"
        ):

            return {
                "status": "PASS",
                "score": score
            }


        return {

            "status": level,

            "symbol": symbol,

            "score": score,

            "price": price,

            "chg": item["chg"],

            "loc": location,

            "bp": buyer_percent,

            "vr": volume_ratio,

            "vr5": volume5_ratio,

            "rv": current_rsi,

            "ad": adx_value,

            "dist": distance_to_resistance,

            "ema": ema_up,

            "macd": (
                macd_histogram
                >
                previous_macd_histogram
            ),

            "squeeze": squeeze,

            "hl": higher_low,

            "breakout": breakout

        }


    except Exception as e:

        log.debug(
            "%s: %s",
            symbol,
            e
        )

        return {
            "status": "error"
        }


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def message(result):

    if result["status"] == "VERY":

        title = (
            "🔥 ÇOK GÜÇLÜ AL"
        )

    else:

        title = (
            "🟢 AL"
        )


    reasons = []


    if result["breakout"]:

        reasons.append(
            "Direnç kırıldı"
        )

    elif result["dist"] <= 0.35:

        reasons.append(
            f"Direnç %{result['dist']:.2f}"
        )


    if result["vr"] >= 1.5:

        reasons.append(
            f"1m hacim {result['vr']:.1f}x"
        )


    if result["vr5"] >= 1.5:

        reasons.append(
            f"5m hacim {result['vr5']:.1f}x"
        )


    if result["bp"] >= 65:

        reasons.append(
            f"Alıcı %{result['bp']:.0f}"
        )


    if result["ema"]:

        reasons.append(
            "EMA trend"
        )


    if result["macd"]:

        reasons.append(
            "MACD güçleniyor"
        )


    if result["hl"]:

        reasons.append(
            "Higher-Low"
        )


    if result["squeeze"]:

        reasons.append(
            "BB sıkışma"
        )


    return (
        "🐋 BALİNA RADARI V21\n\n"

        f"{title}\n\n"

        f"🪙 #{result['symbol']}\n"

        f"💰 {result['price']:.8g}\n"

        f"💪 Güç: {result['score']}/100\n\n"

        f"📊 1m Hacim: "
        f"{result['vr']:.2f}x | "
        f"5m: {result['vr5']:.2f}x\n"

        f"🛒 Alıcı: "
        f"%{result['bp']:.0f}\n"

        f"🎯 Direnç: "
        f"%{result['dist']:.2f}\n"

        f"📈 RSI: "
        f"{result['rv']:.0f} | "
        f"ADX: {result['ad']:.0f}\n"

        f"🚀 Kırılım: "
        f"{'✅' if result['breakout'] else '⏳'}\n\n"

        f"🔎 "
        f"{' • '.join(reasons[:7])}\n\n"

        (
            "🚀 Güçlü teyit."
            if result["status"] == "VERY"
            else
            "🎯 Alım teyidi oluştu."
        )
        )
# ============================================================
# TARAMA
# ============================================================

def scan():

    start = time.time()

    data = tickers()

    if not data:
        return True


    # --------------------------------------------------------
    # TÜM TRY ADAYLARI
    # --------------------------------------------------------

    all_candidates = candidates(
        data
    )


    # --------------------------------------------------------
    # ÖN ELEME
    #
    # Önce hacim + günlük hareket üzerinden
    # daha anlamlı adayları seçiyoruz.
    #
    # Böylece 400+ coin için aynı anda
    # 1m + 5m istemiyoruz.
    # --------------------------------------------------------

    symbols = shortlist(
        all_candidates
    )


    signals = []

    stats = {}


    # --------------------------------------------------------
    # PARALEL ANALİZ
    # --------------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as executor:

        jobs = [
            executor.submit(
                analyze,
                item
            )
            for item in symbols
        ]


        for job in as_completed(jobs):

            result = job.result()

            status = result.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0)
                +
                1
            )


            if status in (
                "BUY",
                "VERY"
            ):

                signals.append(
                    result
                )


    # --------------------------------------------------------
    # SIRALAMA
    # --------------------------------------------------------

    rank = {
        "BUY": 1,
        "VERY": 2
    }


    signals.sort(
        key=lambda x: (
            rank[x["status"]],
            x["score"]
        ),
        reverse=True
    )


    # --------------------------------------------------------
    # TELEGRAM
    #
    # SADECE AL / VERY
    #
    # INTERNAL / SETUP BURAYA HİÇ GELMEZ.
    # --------------------------------------------------------

    sent = 0


    for result in signals[
        :MAX_SIGNALS
    ]:


        if not DBS.can_send(
            result["symbol"],
            result["status"]
        ):

            continue


        if telegram(
            message(result)
        ):

            DBS.put(
                result["symbol"],
                result["score"],
                result["status"],
                result["status"],
                sent=time.time()
            )

            sent += 1


        time.sleep(
            0.3
        )


    elapsed = (
        time.time()
        -
        start
    )


    errors = stats.get(
        "error",
        0
    )


    log.info(
        "V21 | TRY:%d/%d | AL:%d | "
        "VERY:%d | Hata:%d | "
        "Gonder:%d | %.1fs",

        len(symbols),

        len(all_candidates),

        stats.get(
            "BUY",
            0
        ),

        stats.get(
            "VERY",
            0
        ),

        errors,

        sent,

        elapsed
    )


    # --------------------------------------------------------
    # SİSTEM ÇOK YAVAŞLARSA
    # veya çok fazla hata varsa
    # sonraki turda backoff uygulanır.
    # --------------------------------------------------------

    return (
        errors
        /
        max(
            1,
            len(symbols)
        )
        > 0.30
        or
        elapsed
        >
        SCAN_INTERVAL * 1.25
    )


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():

    return (
        "🐋 Balina Radarı V21 Aktif"
    )


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V21",
        "base": BASE,
        "scan_interval": SCAN_INTERVAL
    }


# ============================================================
# MARKET DOĞRULAMA
# ============================================================

def validate_market():

    info = exchange_info()

    symbols = {
        item.get("symbol")
        for item in info.get(
            "symbols",
            []
        )
    }


    try_count = sum(
        symbol.endswith("TRY")
        for symbol in symbols
        if symbol
    )


    if try_count <= 0:

        raise RuntimeError(
            f"BASE {BASE} üzerinde "
            "TRY marketi bulunamadı. "
            "BINANCE_TR_BASE kontrol edilmeli."
        )


    log.info(
        "V21 | Binance TR market "
        "doğrulandı | TRY:%d",
        try_count
    )


# ============================================================
# ANA DÖNGÜ
# ============================================================

def loop():

    log.info(
        "🐋 BALİNA RADARI V21 "
        "başlatılıyor..."
    )


    # --------------------------------------------------------
    # EN ÖNEMLİ KONTROL:
    #
    # API gerçekten TRY marketi veriyor mu?
    #
    # Vermiyorsa sistem sessizce yanlış marketi taramaz.
    # --------------------------------------------------------

    try:

        validate_market()

    except Exception as e:

        log.exception(
            "MARKET DOĞRULAMA HATASI: %s",
            e
        )

        return


    # Başlangıç bildirimi.
    # Bu bir sinyal değildir.

    if TOKEN and CHAT:

        telegram(
            "🐋 BALİNA RADARI V21 AKTİF\n"
            "🟢 AL → 🔥 ÇOK GÜÇLÜ AL"
        )


    while True:

        started = time.time()


        try:

            backoff = scan()

        except Exception:

            log.exception(
                "Tarama döngüsü hatası"
            )

            backoff = True


        elapsed = (
            time.time()
            -
            started
        )


        if backoff:

            time.sleep(
                max(
                    180,
                    SCAN_INTERVAL * 3
                )
            )

        else:

            time.sleep(
                max(
                    1,
                    SCAN_INTERVAL
                    -
                    elapsed
                )
        )
# ============================================================
# THREAD + SERVER
# ============================================================

Thread(
    target=loop,
    daemon=True,
    name="balina-v21"
).start()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),
        use_reloader=False
)
