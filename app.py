import os
import time
import threading
from datetime import datetime, timezone
from statistics import mean

import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ============================================================
# V31 ERKEN AL RADARI
# ============================================================

SYMBOL_API = "https://www.binance.tr"
MARKET_API = "https://api.binance.me"

INTERVAL = os.getenv("INTERVAL", "5m")
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "300"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))

MIN_SCORE = float(os.getenv("MIN_SCORE", "75"))
EARLY_BUY_SCORE = float(
    os.getenv("EARLY_BUY_SCORE", "82")
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID", ""
).strip()

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(Linux; Android 10) "
        "AppleWebKit/537.36 "
        "Chrome/120.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json",
    "Connection": "keep-alive",
})

last_alert = {}
scanner_started = False
scanner_lock = threading.Lock()


def clean_symbol(symbol):
    return str(symbol).replace("_", "").upper()


def tr_symbol(symbol):
    s = str(symbol).upper()

    if "_" in s:
        return s

    if s.endswith("TRY"):
        return s[:-3] + "_TRY"

    return s


# ============================================================
# TRY SEMBOLLERİ
# ============================================================

def get_try_symbols():

    url = (
        f"{SYMBOL_API}"
        "/open/v1/common/symbols"
    )

    print(
        f"[V31 API] Symbol listesi: {url}",
        flush=True
    )

    r = SESSION.get(
        url,
        timeout=20
    )

    print(
        f"[V31 API] Symbol HTTP={r.status_code}",
        flush=True
    )

    r.raise_for_status()

    payload = r.json()

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Symbol API beklenmeyen cevap verdi."
        )

    data = payload.get(
        "data",
        {}
    )

    if isinstance(data, dict):
        items = data.get(
            "list",
            []
        )
    else:
        items = data

    result = []

    for x in items:

        if not isinstance(x, dict):
            continue

        symbol = str(
            x.get("symbol", "")
        )

        quote = str(
            x.get("quoteAsset", "")
        )

        trading = int(
            x.get(
                "spotTradingEnable",
                1
            )
        )

        symbol_type = int(
            x.get(
                "type",
                1
            )
        )

        if (
            quote == "TRY"
            and trading
            and symbol_type == 1
        ):
            result.append(symbol)

    print(
        "[V31 API] Symbol listesi OK | "
        f"TRY MAIN={len(result)}",
        flush=True
    )

    return result


# ============================================================
# KLINE
# ============================================================

def get_klines(
    symbol,
    limit=KLINE_LIMIT
):

    sym = clean_symbol(symbol)

    url = (
        f"{MARKET_API}"
        "/api/v1/klines"
    )

    params = {
        "symbol": sym,
        "interval": INTERVAL,
        "limit": min(
            int(limit),
            1000
        )
    }

    print(
        f"[V31 KLINE] {sym} -> {url}",
        flush=True
    )

    try:

        r = SESSION.get(
            url,
            params=params,
            timeout=15
        )

    except Exception as exc:

        raise RuntimeError(
            f"{sym}: bağlantı hatası: {exc}"
        ) from exc

    print(
        f"[V31 KLINE] {sym} "
        f"HTTP={r.status_code}",
        flush=True
    )

    if r.status_code != 200:

        body = r.text[:500].replace(
            "\n",
            " "
        )

        raise RuntimeError(
            f"{sym}: HTTP {r.status_code} | "
            f"{body}"
        )

    try:
        payload = r.json()
    except Exception as exc:
        raise RuntimeError(
            f"{sym}: JSON okunamadı."
        ) from exc

    if isinstance(payload, dict):

        code = payload.get(
            "code"
        )

        if code not in (
            None,
            0,
            "0"
        ):

            raise RuntimeError(
                f"{sym}: API code={code} "
                f"msg={payload.get('msg')}"
            )

        data = payload.get(
            "data",
            []
        )

    elif isinstance(payload, list):

        data = payload

    else:

        raise RuntimeError(
            f"{sym}: beklenmeyen cevap."
        )

    rows = []

    for x in data:

        if not isinstance(x, list):
            continue

        if len(x) < 10:
            continue

        try:

            rows.append({
                "time": int(x[0]),
                "open": float(x[1]),
                "high": float(x[2]),
                "low": float(x[3]),
                "close": float(x[4]),
                "volume": float(x[5]),
                "quote_volume": float(x[7]),
            })

        except Exception:
            continue

    if len(rows) < 60:

        raise RuntimeError(
            f"{sym}: yeterli mum yok "
            f"({len(rows)})"
        )

    return rows


# ============================================================
# MATEMATİK
# ============================================================

def ema_series(
    values,
    period
):

    if not values:
        return []

    k = 2.0 / (
        period + 1.0
    )

    out = [values[0]]

    for v in values[1:]:

        out.append(
            v * k
            + out[-1] * (
                1 - k
            )
        )

    return out


def rsi(
    values,
    period=14
):

    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(
        len(values) - period,
        len(values)
    ):

        d = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(d, 0)
        )

        losses.append(
            max(-d, 0)
        )

    ag = mean(gains)
    al = mean(losses)

    if al == 0:

        return (
            100.0
            if ag > 0
            else 50.0
        )

    return (
        100.0
        - 100.0 / (
            1.0 + ag / al
        )
    )


def pct(a, b):

    if b == 0:
        return 0.0

    return (
        a / b - 1.0
    ) * 100.0


def clamp(
    v,
    lo,
    hi
):

    return max(
        lo,
        min(hi, v)
    )


# ============================================================
# V31 SKOR
# ============================================================

def score_signal(
    candles,
    i
):

    if (
        i < 55
        or i >= len(candles)
    ):
        return None

    c = candles[
        :i + 1
    ]

    closes = [
        x["close"]
        for x in c
    ]

    lows = [
        x["low"]
        for x in c
    ]

    vols = [
        x["volume"]
        for x in c
    ]

    p = closes[-1]

    if p <= 0:
        return None

    # ERKEN KIVRIM / 30

    w6 = closes[-6:]
    prev6 = closes[-12:-6]

    slope6 = pct(
        w6[-1],
        w6[0]
    )

    prev_slope6 = pct(
        prev6[-1],
        prev6[0]
    )

    turn_strength = (
        slope6
        - prev_slope6
    )

    early = 0.0

    if prev_slope6 < -0.20:
        early += 8

    if turn_strength > 0.15:
        early += 8

    if slope6 > -0.15:
        early += 5

    if slope6 > 0.05:
        early += 4

    last3 = pct(
        closes[-1],
        closes[-4]
    )

    if last3 > 1.8:
        early -= 7

    elif last3 > 1.0:
        early -= 3

    early = clamp(
        early,
        0,
        30
    )

    # DİP / 25

    look = closes[-50:]

    lo50 = min(look)
    hi50 = max(look)

    rng = hi50 - lo50

    pos = (
        0.5
        if rng <= 0
        else (p - lo50) / rng
    )

    dip = 0.0

    if pos <= 0.25:
        dip += 17

    elif pos <= 0.38:
        dip += 14

    elif pos <= 0.52:
        dip += 9

    elif pos <= 0.65:
        dip += 4

    rr = rsi(
        closes,
        14
    )

    if 35 <= rr <= 50:
        dip += 6

    elif 50 < rr <= 58:
        dip += 3

    elif rr > 68:
        dip -= 5

    recent_low = min(
        lows[-8:]
    )

    older_low = min(
        lows[-24:-8]
    )

    if (
        recent_low
        >= older_low * 0.995
    ):
        dip += 2

    dip = clamp(
        dip,
        0,
        25
    )

    # YAPI / 20

    structure = 0.0

    e9 = ema_series(
        closes,
        9
    )

    e21 = ema_series(
        closes,
        21
    )

    if e9[-1] > e21[-1]:
        structure += 6

    if e9[-1] >= e9[-3]:
        structure += 4

    if e21[-1] >= e21[-5]:
        structure += 3

    l1 = min(
        lows[-6:-3]
    )

    l2 = min(
        lows[-3:]
    )

    if l2 >= l1 * 0.998:
        structure += 4

    if p >= e9[-1] * 0.999:
        structure += 3

    structure = clamp(
        structure,
        0,
        20
    )

    # HACİM / 15

    avg20 = mean(
        vols[-21:-1]
    )

    vr = (
        vols[-1] / avg20
        if avg20 > 0
        else 0.0
    )

    volume = 0.0

    if vr >= 1.25:
        volume += 7

    elif vr >= 1.10:
        volume += 5

    elif vr >= 0.90:
        volume += 3

    v1 = mean(
        vols[-4:-2]
    )

    v2 = mean(
        vols[-2:]
    )

    if v2 > v1 * 1.05:
        volume += 4

    spike = vr >= 3.0

    if spike:
        volume -= 2

    volume = clamp(
        volume,
        0,
        15
    )

    # MOMENTUM / 10

    momentum = 0.0

    ret3 = pct(
        closes[-1],
        closes[-4]
    )

    ret6 = pct(
        closes[-1],
        closes[-7]
    )

    if ret3 > 0:
        momentum += 4

    if ret3 > 0.20:
        momentum += 2

    if ret6 > -0.20:
        momentum += 2

    if ret6 > 0:
        momentum += 2

    momentum = clamp(
        momentum,
        0,
        10
    )
