import os
import time
import threading
from datetime import datetime, timezone
from statistics import mean

import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ============================================================
# V32 ERKEN HAREKET MOTORU
# AMAÇ:
# Yükseliş başladıktan sonra değil,
# mümkünse yükselişten 1-3 adet 5 dakikalık mum önce
# oluşan yapıyı geçmiş verilerden tespit etmek.
# ============================================================

SYMBOL_API = "https://www.binance.tr"
MARKET_API = "https://api.binance.me"

INTERVAL = os.getenv("INTERVAL", "5m")
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "1000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(Linux; Android 10) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json",
    "Connection": "keep-alive",
})

scanner_started = False
scanner_lock = threading.Lock()


# ============================================================
# YARDIMCI
# ============================================================

def clean_symbol(symbol):
    return str(symbol).replace("_", "").upper()


def tr_symbol(symbol):
    s = str(symbol).upper()

    if "_" in s:
        return s

    if s.endswith("TRY"):
        return s[:-3] + "_TRY"

    return s


def pct(a, b):
    if b == 0:
        return 0.0

    return (a / b - 1.0) * 100.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ============================================================
# BINANCE TR TRY SEMBOLLERİ
# ============================================================

def get_try_symbols():

    url = (
        f"{SYMBOL_API}"
        "/open/v1/common/symbols"
    )

    print(
        f"[V32 SYMBOL] {url}",
        flush=True
    )

    r = SESSION.get(
        url,
        timeout=20
    )

    print(
        f"[V32 SYMBOL] HTTP={r.status_code}",
        flush=True
    )

    r.raise_for_status()

    payload = r.json()

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Sembol API beklenmeyen cevap verdi."
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
        ).upper()

        quote = str(
            x.get("quoteAsset", "")
        ).upper()

        try:
            trading = int(
                x.get(
                    "spotTradingEnable",
                    1
                )
            )
        except Exception:
            trading = 1

        if (
            quote == "TRY"
            and trading
        ):
            result.append(symbol)

    result = sorted(
        set(result)
    )

    print(
        f"[V32 SYMBOL] TRY çiftleri={len(result)}",
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
        f"[V32 KLINE] {sym} -> {url}",
        flush=True
    )

    r = SESSION.get(
        url,
        params=params,
        timeout=20
    )

    print(
        f"[V32 KLINE] {sym} HTTP={r.status_code}",
        flush=True
    )

    if r.status_code != 200:
        raise RuntimeError(
            f"{sym}: HTTP {r.status_code} | "
            f"{r.text[:300]}"
        )

    payload = r.json()

    if isinstance(payload, dict):

        code = payload.get("code")

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
            f"{sym}: beklenmeyen Kline cevabı"
        )

    rows = []

    for x in data:

        if (
            not isinstance(x, list)
            or len(x) < 10
        ):
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

    if len(rows) < 80:
        raise RuntimeError(
            f"{sym}: yeterli mum yok "
            f"({len(rows)})"
        )

    return rows


# ============================================================
# EMA
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

    for value in values[1:]:

        out.append(
            value * k
            + out[-1] * (
                1.0 - k
            )
        )

    return out


# ============================================================
# RSI
# ============================================================

def rsi(
    values,
    period=14
):

    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    start = len(values) - period

    for i in range(
        start,
        len(values)
    ):

        d = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(d, 0.0)
        )

        losses.append(
            max(-d, 0.0)
        )

    avg_gain = mean(gains)
    avg_loss = mean(losses)

    if avg_loss == 0:

        if avg_gain > 0:
            return 100.0

        return 50.0

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100.0
        - 100.0 / (
            1.0 + rs
        )
    )


# ============================================================
# V32 ÖZELLİKLERİ
# ============================================================

def calculate_features(
    candles,
    i
):

    if i < 60:
        return None

    c = candles[:i + 1]

    closes = [
        x["close"]
        for x in c
    ]

    highs = [
        x["high"]
        for x in c
    ]

    lows = [
        x["low"]
        for x in c
    ]

    volumes = [
        x["volume"]
        for x in c
    ]

    price = closes[-1]

    if price <= 0:
        return None

    # --------------------------------------------------------
    # FİYAT YAPISI
    # --------------------------------------------------------

    ret1 = pct(
        closes[-1],
        closes[-2]
    )

    ret3 = pct(
        closes[-1],
        closes[-4]
    )

    ret6 = pct(
        closes[-1],
        closes[-7]
    )

    ret12 = pct(
        closes[-1],
        closes[-13]
    )

    # --------------------------------------------------------
    # DÜŞÜŞÜN YAVAŞLAMASI
    # --------------------------------------------------------

    prev6 = closes[-12:-6]
    last6 = closes[-6:]

    prev6_ret = pct(
        prev6[-1],
        prev6[0]
    )

    last6_ret = pct(
        last6[-1],
        last6[0]
    )

    turn_strength = (
        last6_ret
        - prev6_ret
    )

    # --------------------------------------------------------
    # 50 MUM İÇİNDEKİ KONUM
    # --------------------------------------------------------

    window50 = closes[-50:]

    low50 = min(window50)
    high50 = max(window50)

    price_range = (
        high50 - low50
    )

    if price_range > 0:

        position50 = (
            price - low50
        ) / price_range

    else:

        position50 = 0.5

    # --------------------------------------------------------
    # HACİM
    # --------------------------------------------------------

    avg20 = mean(
        volumes[-21:-1]
    )

    volume_ratio = (
        volumes[-1] / avg20
        if avg20 > 0
        else 0.0
    )

    avg_prev3 = mean(
        volumes[-6:-3]
    )

    avg_last3 = mean(
        volumes[-3:]
    )

    if avg_prev3 > 0:

        volume_acceleration = (
            avg_last3
            / avg_prev3
        )

    else:

        volume_acceleration = 1.0

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    ema9 = ema_series(
        closes,
        9
    )

    ema21 = ema_series(
        closes,
        21
    )

    ema9_slope = pct(
        ema9[-1],
        ema9[-4]
    )

    ema21_slope = pct(
        ema21[-1],
        ema21[-6]
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi14 = rsi(
        closes,
        14
    )

    # --------------------------------------------------------
    # DİP YAPISI
    # --------------------------------------------------------

    recent_low = min(
        lows[-8:]
    )

    previous_low = min(
        lows[-24:-8]
    )

    if previous_low > 0:

        higher_low_ratio = (
            recent_low
            / previous_low
        )

    else:

        higher_low_ratio = 1.0

    # --------------------------------------------------------
    # SON MUMLARDAKİ HAREKET
    # --------------------------------------------------------

    last3_high = max(
        highs[-3:]
    )

    previous6_high = max(
        highs[-9:-3]
    )

    if previous6_high > 0:

        breakout_distance = (
            price
            / previous6_high
            - 1.0
        ) * 100.0

    else:

        breakout_distance = 0.0

    return {

        "time": c[-1]["time"],

        "price": price,

        "ret1": ret1,
        "ret3": ret3,
        "ret6": ret6,
        "ret12": ret12,

        "prev6_ret": prev6_ret,
        "last6_ret": last6_ret,
        "turn_strength": turn_strength,

        "position50": position50,

        "volume_ratio": volume_ratio,
        "volume_acceleration":
            volume_acceleration,

        "ema9_slope":
            ema9_slope,

        "ema21_slope":
            ema21_slope,

        "ema9_above_21":
            ema9[-1] > ema21[-1],

        "price_above_ema9":
            price >= ema9[-1],

        "rsi14":
            rsi14,

        "higher_low_ratio":
            higher_low_ratio,

        "breakout_distance":
            breakout_distance,

        "recent_low":
            recent_low,

        "previous_low":
            previous_low,

        "last3_high":
            last3_high,
    }


# ============================================================
# GERÇEK ERKEN HAREKET HEDEFİ
# ============================================================

def future_result(
    candles,
    i
):

    if i + 5 >= len(candles):
        return None

    entry = candles[i]["close"]

    if entry <= 0:
        return None

    future = candles[
        i + 1:i + 6
    ]

    max_gain = max(
        pct(
            x["high"],
            entry
        )
        for x in future
    )

    max_drawdown = min(
        pct(
            x["low"],
            entry
        )
        for x in future
    )

    close1 = pct(
        candles[i + 1]["close"],
        entry
    )

    close3 = pct(
        candles[i + 3]["close"],
        entry
    )

    close5 = pct(
        candles[i + 5]["close"],
        entry
    )

    # --------------------------------------------------------
    # HEDEF:
    #
    # Önümüzdeki 1-3 mumda en az +%1 hareket
    # oluşmuşsa "erken pozitif hareket".
    #
    # Daha sonra bu eşiği gerçek Binance TR verisine
    # göre optimize edeceğiz.
    # --------------------------------------------------------

    early_success = (
        max(
            pct(
                x["high"],
                entry
            )
            for x in candles[
                i + 1:i + 4
            ]
        ) >= 1.0
    )

    return {

        "max_gain":
            round(max_gain, 4),

        "max_drawdown":
            round(max_drawdown, 4),

        "return_1":
            round(close1, 4),

        "return_3":
            round(close3, 4),

        "return_5":
            round(close5, 4),

        "early_success":
            bool(early_success),
    }


# ============================================================
# GEÇMİŞ VERİ ANALİZİ
# ============================================================

def analyze_symbol(
    symbol
):

    candles = get_klines(
        symbol,
        KLINE_LIMIT
    )

    samples = []

    # Son 5 mum gelecekteki sonucu ölçmek için kullanılmaz.
    last_index = (
        len(candles) - 6
    )

    for i in range(
        60,
        last_index
    ):

        features = calculate_features(
            candles,
            i
        )

        if features is None:
            continue

        result = future_result(
            candles,
            i
        )

        if result is None:
            continue

        samples.append({
            **features,
            **result,
        })

    total = len(samples)

    if total == 0:

        return {
            "symbol":
                tr_symbol(symbol),
            "samples": 0,
            "success_rate": 0.0,
            "status": "NO_DATA",
            "examples": [],
        }

    successes = sum(
        1
        for x in samples
        if x["early_success"]
    )

    success_rate = (
        successes
        / total
        * 100.0
    )

    # En son 20 örneği döndür.
    examples = samples[-20:]

    return {

        "symbol":
            tr_symbol(symbol),

        "samples":
            total,

        "successes":
            successes,

        "failures":
            total - successes,

        "success_rate":
            round(
                success_rate,
                2
            ),

        "average_max_gain":
            round(
                mean(
                    x["max_gain"]
                    for x in samples
                ),
                4
            ),

        "average_drawdown":
            round(
                mean(
                    x["max_drawdown"]
                    for x in samples
                ),
                4
            ),

        "status":
            "OK",

        "examples":
            examples,
}
# ============================================================
# V32 - GEÇMİŞ HAREKET ANALİZİ + API
# ============================================================

def analyze_symbol(symbol):
    candles = get_klines(symbol, KLINE_LIMIT)
    samples = []

    for i in range(60, len(candles) - 6):
        features = calculate_features(candles, i)

        if features is None:
            continue

        result = future_result(candles, i)

        if result is None:
            continue

        samples.append({
            **features,
            **result
        })

    if not samples:
        return {
            "symbol": tr_symbol(symbol),
            "samples": 0,
            "success_rate": 0,
            "status": "NO_DATA",
            "examples": []
        }

    success = sum(
        1 for x in samples
        if x["early_success"]
    )

    return {
        "symbol": tr_symbol(symbol),
        "samples": len(samples),
        "successes": success,
        "failures": len(samples) - success,
        "success_rate": round(
            success / len(samples) * 100,
            2
        ),
        "average_max_gain": round(
            mean(x["max_gain"] for x in samples),
            4
        ),
        "average_drawdown": round(
            mean(x["max_drawdown"] for x in samples),
            4
        ),
        "examples": samples[-20:],
        "status": "OK"
    }


# ============================================================
# V32 ERKEN ADAY PUANI
# ============================================================

def v32_score(candles, i):

    f = calculate_features(candles, i)

    if f is None:
        return None

    score = 0.0

    # --------------------------------------------------------
    # 1. Düşüşün yavaşlaması
    # --------------------------------------------------------

    if f["prev6_ret"] < -0.30:
        score += 12

    if f["turn_strength"] > 0.20:
        score += 12

    elif f["turn_strength"] > 0.10:
        score += 7

    # --------------------------------------------------------
    # 2. Dip bölgesi
    # --------------------------------------------------------

    if f["position50"] <= 0.30:
        score += 12

    elif f["position50"] <= 0.45:
        score += 8

    elif f["position50"] <= 0.60:
        score += 4

    # --------------------------------------------------------
    # 3. Higher-low
    # --------------------------------------------------------

    if f["higher_low_ratio"] >= 1.002:
        score += 10

    elif f["higher_low_ratio"] >= 0.998:
        score += 5

    # --------------------------------------------------------
    # 4. Hacim uyanışı
    # --------------------------------------------------------

    if 1.10 <= f["volume_ratio"] <= 2.50:
        score += 8

    if f["volume_acceleration"] > 1.10:
        score += 8

    # --------------------------------------------------------
    # 5. EMA yapısı
    # --------------------------------------------------------

    if f["ema9_slope"] > 0:
        score += 5

    if f["ema21_slope"] > -0.05:
        score += 4

    if f["price_above_ema9"]:
        score += 4

    # --------------------------------------------------------
    # 6. RSI
    # --------------------------------------------------------

    if 35 <= f["rsi14"] <= 55:
        score += 7

    elif 55 < f["rsi14"] <= 62:
        score += 3

    # --------------------------------------------------------
    # 7. Henüz kopmamış olması
    # --------------------------------------------------------

    if f["breakout_distance"] < 0.50:
        score += 8

    elif f["breakout_distance"] < 1.00:
        score += 4

    # --------------------------------------------------------
    # GEÇ SİNYAL CEZASI
    # --------------------------------------------------------

    if f["ret3"] > 1.50:
        score -= 15

    elif f["ret3"] > 1.00:
        score -= 8

    if f["ret6"] > 3.00:
        score -= 10

    # Aşırı hacim spike'ı
    if f["volume_ratio"] > 3.50:
        score -= 8

    score = clamp(score, 0, 100)

    if score >= 78:
        status = "V32_EARLY"

    elif score >= 68:
        status = "V32_WATCH"

    else:
        status = "NO_SIGNAL"

    return {
        **f,
        "score": round(score, 2),
        "status": status
    }


# ============================================================
# CANLI TARAMA
# ============================================================

def scan_once():

    found = []

    try:
        symbols = get_try_symbols()

    except Exception as exc:

        print(
            "[V32] Sembol listesi alınamadı:",
            exc,
            flush=True
        )

        return found

    for symbol in symbols:

        try:

            candles = get_klines(
                symbol,
                min(KLINE_LIMIT, 300)
            )

            i = len(candles) - 1

            signal = v32_score(
                candles,
                i
            )

            if signal is None:
                continue

            if signal["status"] not in (
                "V32_EARLY",
                "V32_WATCH"
            ):
                continue

            item = {
                "symbol":
                    tr_symbol(symbol),
                **signal
            }

            print(
                "[V32 ADAY]",
                item,
                flush=True
            )

            found.append(item)

        except Exception as exc:

            print(
                f"[V32] {symbol}: {exc}",
                flush=True
            )

    print(
        f"[V32] Tarama tamamlandı | "
        f"aday={len(found)}",
        flush=True
    )

    return found


# ============================================================
# FLASK
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "status": "ok",
        "version": "V32",
        "message":
            "V32 Erken Hareket Motoru çalışıyor",
        "interval": INTERVAL,
        "scan_interval_seconds":
            SCAN_INTERVAL
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "version": "V32"
    })


@app.route("/api/test/<symbol>")
def api_test(symbol):

    try:

        result = analyze_symbol(
            symbol
        )

        return jsonify({
            "status": "ok",
            "result": result
        })

    except Exception as exc:

        return jsonify({
            "status": "error",
            "message": str(exc)
        }), 500


@app.route("/api/scan")
def api_scan():

    try:

        result = scan_once()

        return jsonify({
            "status": "ok",
            "result": result
        })

    except Exception as exc:

        return jsonify({
            "status": "error",
            "message": str(exc)
        }), 500


# ============================================================
# ARKA PLAN TARAMASI
# ============================================================

def scanner_loop():

    print(
        "[V32 WORKER] Tarama başlıyor...",
        flush=True
    )

    while True:

        started = time.time()

        try:
            scan_once()

        except Exception as exc:

            print(
                "[V32 WORKER] Hata:",
                repr(exc),
                flush=True
            )

        elapsed = time.time() - started

        wait = max(
            10,
            SCAN_INTERVAL - elapsed
        )

        time.sleep(wait)


def start_scanner():

    global scanner_started

    with scanner_lock:

        if scanner_started:
            return

        scanner_started = True

    thread = threading.Thread(
        target=scanner_loop,
        daemon=True
    )

    thread.start()


# ============================================================
# BAŞLAT
# ============================================================

start_scanner()


if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
        )
