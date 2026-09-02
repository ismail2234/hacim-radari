from __future__ import annotations

import os
import time
import threading

import requests
from flask import Flask, jsonify

from telegram_notifier import TelegramNotifier

app = Flask(__name__)

# V33 - Erken Kıvrım Motoru
# Hedef: Binance TR TRY paritelerinde yükselişten önceki 1-3 adet
# 5 dakikalık mumluk yapıyı yakalamak.
# Sadece kapanmış mum sinyal üretir.

SYMBOL_API = os.getenv("SYMBOL_API", "https://www.binance.tr")
MARKET_API = os.getenv("MARKET_API", "https://api.binance.me")
INTERVAL = os.getenv("INTERVAL", "5m")
KLINE_LIMIT = min(int(os.getenv("KLINE_LIMIT", "300")), 1000)
SCAN_INTERVAL = max(int(os.getenv("SCAN_INTERVAL", "300")), 60)

MIN_SCORE = float(os.getenv("MIN_SCORE", "78"))
MIN_TR_VOLUME = float(os.getenv("MIN_TR_VOLUME", "10000"))
MAX_EARLY_MOVE = float(os.getenv("MAX_EARLY_MOVE", "1.50"))
MAX_VOLUME_RATIO = float(os.getenv("MAX_VOLUME_RATIO", "4.00"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "1800"))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "HacimRadari-V33/1.0",
    "Accept": "application/json",
    "Connection": "keep-alive",
})

notifier = TelegramNotifier()
last_alerts: dict[str, float] = {}
scanner_started = False
scanner_lock = threading.Lock()


def clean_symbol(symbol: str) -> str:
    return str(symbol).replace("_", "").upper()


def tr_symbol(symbol: str) -> str:
    s = str(symbol).upper()
    if "_" in s:
        return s
    return s[:-3] + "_TRY" if s.endswith("TRY") else s


def pct(a: float, b: float) -> float:
    return 0.0 if b == 0 else (a / b - 1.0) * 100.0


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def mean_safe(values, default=0.0):
    # Performance optimization: sum(xs) / len(xs) avoids statistics.mean overhead (~40x faster in Python 3.12)
    if not values:
        return default
    if not isinstance(values, (list, tuple)):
        values = list(values)
        if not values:
            return default
    return sum(values) / len(values)


def ema_series(values, period):
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1.0 - k))
    return out


def rsi_series(values, period=14):
    if len(values) <= period:
        return [50.0] * len(values)

    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    avg_gain = mean_safe(gains[1:period + 1])
    avg_loss = mean_safe(losses[1:period + 1])
    result = [50.0] * period

    for i in range(period, len(values)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            value = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            value = 100.0 - 100.0 / (1.0 + rs)
        result.append(value)

    return result


def get_try_symbols():
    response = SESSION.get(
        f"{SYMBOL_API}/open/v1/common/symbols",
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    items = data.get("list", []) if isinstance(data, dict) else data

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).upper()
        quote = str(item.get("quoteAsset", "")).upper()
        try:
            enabled = int(item.get("spotTradingEnable", 1))
        except Exception:
            enabled = 1
        if quote == "TRY" and enabled:
            result.append(symbol)

    return sorted(set(result))


def get_klines(symbol, limit=KLINE_LIMIT):
    sym = clean_symbol(symbol)
    response = SESSION.get(
        f"{MARKET_API}/api/v1/klines",
        params={
            "symbol": sym,
            "interval": INTERVAL,
            "limit": min(int(limit), 1000),
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, dict):
        code = payload.get("code")
        if code not in (None, 0, "0"):
            raise RuntimeError(
                f"{sym}: API code={code} msg={payload.get('msg')}"
            )
        data = payload.get("data", [])
    elif isinstance(payload, list):
        data = payload
    else:
        raise RuntimeError(f"{sym}: beklenmeyen Kline cevabı")

    rows = []
    for x in data:
        if not isinstance(x, list) or len(x) < 10:
            continue
        try:
            rows.append({
                "time": int(x[0]),
                "open": float(x[1]),
                "high": float(x[2]),
                "low": float(x[3]),
                "close": float(x[4]),
                "volume": float(x[5]),
                # x[7] quote asset hacmidir; TRY paritesinde TRY hacmi.
                "quote_volume": float(x[7]),
                "close_time": int(x[6]),
            })
        except (TypeError, ValueError):
            continue

    if len(rows) < 80:
        raise RuntimeError(f"{sym}: yeterli mum yok ({len(rows)})")

    return rows


def closed_candles(candles):
    """Son mum hâlâ açıksa çıkar; yalnızca kapanmış mum kullan."""
    if len(candles) < 3:
        return candles

    now_ms = int(time.time() * 1000)
    if candles[-1].get("close_time", 0) >= now_ms:
        return candles[:-1]
    return candles


def calculate_features(candles, i):
    if i < 60:
        return None

    c = candles[:i + 1]
    closes = [x["close"] for x in c]
    highs = [x["high"] for x in c]
    lows = [x["low"] for x in c]
    quote_volumes = [x["quote_volume"] for x in c]

    price = closes[-1]
    if price <= 0:
        return None

    ret1 = pct(closes[-1], closes[-2])
    ret3 = pct(closes[-1], closes[-4])
    ret6 = pct(closes[-1], closes[-7])
    ret12 = pct(closes[-1], closes[-13])

    prev6 = closes[-12:-6]
    last6 = closes[-6:]
    prev6_ret = pct(prev6[-1], prev6[0])
    last6_ret = pct(last6[-1], last6[0])
    turn_strength = last6_ret - prev6_ret

    window50 = closes[-50:]
    low50 = min(window50)
    high50 = max(window50)
    position50 = (
        (price - low50) / (high50 - low50)
        if high50 > low50 else 0.5
    )

    avg20_try = mean_safe(quote_volumes[-21:-1])
    volume_ratio = quote_volumes[-1] / avg20_try if avg20_try > 0 else 0.0

    avg_prev3 = mean_safe(quote_volumes[-6:-3])
    avg_last3 = mean_safe(quote_volumes[-3:])
    volume_acceleration = (
        avg_last3 / avg_prev3 if avg_prev3 > 0 else 1.0
    )

    ema9 = ema_series(closes, 9)
    ema21 = ema_series(closes, 21)
    ema9_slope = pct(ema9[-1], ema9[-4])
    ema21_slope = pct(ema21[-1], ema21[-6])

    rsi_values = rsi_series(closes, 14)
    rsi14 = rsi_values[-1]
    rsi_turn = rsi14 - rsi_values[-4]

    recent_low = min(lows[-8:])
    previous_low = min(lows[-24:-8])
    higher_low_ratio = recent_low / previous_low if previous_low > 0 else 1.0

    previous6_high = max(highs[-9:-3])
    breakout_distance = pct(price, previous6_high) if previous6_high > 0 else 0.0

    body_pcts = [pct(x["close"], x["open"]) for x in c[-3:]]
    last3_body = sum(body_pcts)

    return {
        "time": c[-1]["time"],
        "price": price,
        "try_volume": quote_volumes[-1],
        "ret1": ret1,
        "ret3": ret3,
        "ret6": ret6,
        "ret12": ret12,
        "prev6_ret": prev6_ret,
        "last6_ret": last6_ret,
        "turn_strength": turn_strength,
        "position50": position50,
        "volume_ratio": volume_ratio,
        "volume_acceleration": volume_acceleration,
        "ema9_slope": ema9_slope,
        "ema21_slope": ema21_slope,
        "ema9_above_21": ema9[-1] > ema21[-1],
        "price_above_ema9": price >= ema9[-1],
        "rsi14": rsi14,
        "rsi_turn": rsi_turn,
        "higher_low_ratio": higher_low_ratio,
        "breakout_distance": breakout_distance,
        "last3_body": last3_body,
    }


def v33_score(candles, i):
    f = calculate_features(candles, i)
    if f is None:
        return None

    score = 0.0
    early_score = 0.0
    volume_score = 0.0
    dip_score = 0.0
    momentum_score = 0.0
    structure_score = 0.0
    late_penalty = 0.0
    spike_penalty = 0.0
    breakdown_penalty = 0.0
    divergence_penalty = 0.0

    # ERKEN KIVRIM / 25
    if f["prev6_ret"] <= -0.80:
        early_score += 7
    elif f["prev6_ret"] <= -0.30:
        early_score += 5

    if f["turn_strength"] >= 0.45:
        early_score += 10
    elif f["turn_strength"] >= 0.20:
        early_score += 7
    elif f["turn_strength"] >= 0.10:
        early_score += 4

    if -0.20 < f["ret1"] < 0.70:
        early_score += 4

    if -0.50 <= f["ret3"] <= 0.80:
        early_score += 4

    # HACİM / 25
    if f["try_volume"] >= MIN_TR_VOLUME:
        volume_score += 7

    if 1.15 <= f["volume_ratio"] <= 2.50:
        volume_score += 10
    elif 1.05 <= f["volume_ratio"] < 1.15:
        volume_score += 4
    elif 2.50 < f["volume_ratio"] <= 3.50:
        volume_score += 6

    if f["volume_acceleration"] >= 1.20:
        volume_score += 8
    elif f["volume_acceleration"] >= 1.05:
        volume_score += 4

    # DİP / 16
    if f["position50"] <= 0.25:
        dip_score += 8
    elif f["position50"] <= 0.35:
        dip_score += 6
    elif f["position50"] <= 0.45:
        dip_score += 3

    if f["higher_low_ratio"] >= 1.002:
        dip_score += 8
    elif f["higher_low_ratio"] >= 0.999:
        dip_score += 4

    # MOMENTUM / 15
    if f["ema9_slope"] > 0:
        momentum_score += 5
    if f["ema21_slope"] > -0.10:
        momentum_score += 3
    if 38 <= f["rsi14"] <= 58:
        momentum_score += 4
    elif 58 < f["rsi14"] <= 63:
        momentum_score += 2
    if f["rsi_turn"] > 1.0:
        momentum_score += 3

    # YAPI / 15
    if f["price_above_ema9"]:
        structure_score += 4
    if f["ema9_above_21"]:
        structure_score += 3
    if f["breakout_distance"] < 0.50:
        structure_score += 5
    elif f["breakout_distance"] < 1.00:
        structure_score += 3
    if f["last3_body"] > -0.20:
        structure_score += 3

    # GEÇ SİNYAL CEZALARI
    if f["ret3"] > 1.00:
        late_penalty += 8
    if f["ret3"] > 1.50:
        late_penalty += 7
    if f["ret6"] > 3.00:
        late_penalty += 10

    # TEK MUM HACİM PATLAMASI
    if f["volume_ratio"] > MAX_VOLUME_RATIO:
        spike_penalty += 8
    if f["volume_ratio"] > 6.00:
        spike_penalty += 7

    # KIRILIMDAN SONRA KOVALAMAYI ENGELLE
    if f["breakout_distance"] > 1.50:
        breakdown_penalty += 8
    if f["breakout_distance"] > 3.00:
        breakdown_penalty += 7

    # Fiyat yükselirken RSI düşüyorsa kalite azalır.
    if f["ret3"] > 0.50 and f["rsi_turn"] < -2.0:
        divergence_penalty += 6

    score = clamp(
        early_score
        + volume_score
        + dip_score
        + momentum_score
        + structure_score
        - late_penalty
        - spike_penalty
        - breakdown_penalty
        - divergence_penalty,
        0,
        100,
    )

    if (
        score >= MIN_SCORE
        and f["try_volume"] >= MIN_TR_VOLUME
        and f["ret3"] <= MAX_EARLY_MOVE
        and f["breakout_distance"] <= 1.50
    ):
        status = "V33_EARLY"
    elif score >= 68:
        status = "V33_WATCH"
    else:
        status = "NO_SIGNAL"

    return {
        **f,
        "score": round(score, 2),
        "status": status,
        "early_score": round(early_score, 2),
        "volume_score": round(volume_score, 2),
        "dip_score": round(dip_score, 2),
        "momentum_score": round(momentum_score, 2),
        "structure_score": round(structure_score, 2),
        "late_penalty": round(late_penalty, 2),
        "spike_penalty": round(spike_penalty, 2),
        "breakdown_penalty": round(breakdown_penalty, 2),
        "divergence_penalty": round(divergence_penalty, 2),
    }


def send_alert(item):
    symbol = item["symbol"]
    now = time.time()
    previous = last_alerts.get(symbol, 0)

    if now - previous < ALERT_COOLDOWN:
        return False

    ok = notifier.send_signal(item)
    if ok:
        last_alerts[symbol] = now
    return ok


def scan_once():
    found = []

    try:
        symbols = get_try_symbols()
    except Exception as exc:
        print(f"[V33] Sembol listesi alınamadı: {exc}", flush=True)
        return found

    for symbol in symbols:
        try:
            candles = closed_candles(get_klines(symbol, KLINE_LIMIT))
            if len(candles) < 80:
                continue

            # Kritik: son KAPANMIŞ mum değerlendirilir.
            i = len(candles) - 1
            signal = v33_score(candles, i)

            if signal is None or signal["status"] != "V33_EARLY":
                continue

            item = {"symbol": tr_symbol(symbol), **signal}
            found.append(item)

            print(
                f"[V33 EARLY] {item['symbol']} "
                f"score={item['score']:.1f} "
                f"TRYvol={item['try_volume']:.0f} "
                f"VR={item['volume_ratio']:.2f}x "
                f"ret3={item['ret3']:.2f}%",
                flush=True,
            )

            send_alert(item)

        except Exception as exc:
            print(f"[V33] {symbol}: {exc}", flush=True)

    print(
        f"[V33] Tarama tamamlandı | erken_aday={len(found)}",
        flush=True,
    )
    return found


def future_result(candles, i):
    if i + 5 >= len(candles):
        return None

    entry = candles[i]["close"]
    future_1_3 = candles[i + 1:i + 4]
    future_1_5 = candles[i + 1:i + 6]

    if not future_1_5 or entry <= 0:
        return None

    max_gain_1_3 = max(pct(x["high"], entry) for x in future_1_3)
    max_gain_1_5 = max(pct(x["high"], entry) for x in future_1_5)
    max_drawdown = min(pct(x["low"], entry) for x in future_1_5)

    return {
        "max_gain_1_3": round(max_gain_1_3, 4),
        "max_gain_1_5": round(max_gain_1_5, 4),
        "max_drawdown": round(max_drawdown, 4),
        "early_success_1pct": max_gain_1_3 >= 1.0,
        "early_success_2pct": max_gain_1_3 >= 2.0,
    }


def analyze_symbol(symbol):
    candles = closed_candles(get_klines(symbol, KLINE_LIMIT))
    samples = []

    for i in range(60, len(candles) - 5):
        score = v33_score(candles, i)
        future = future_result(candles, i)
        if score is None or future is None:
            continue
        samples.append({**score, **future})

    early = [x for x in samples if x["status"] == "V33_EARLY"]

    if not samples:
        return {
            "symbol": tr_symbol(symbol),
            "samples": 0,
            "status": "NO_DATA",
            "examples": [],
        }

    return {
        "symbol": tr_symbol(symbol),
        "samples": len(samples),
        "early_candidates": len(early),
        "early_1pct_rate": round(
            mean_safe(x["early_success_1pct"] for x in early) * 100, 2
        ) if early else 0.0,
        "early_2pct_rate": round(
            mean_safe(x["early_success_2pct"] for x in early) * 100, 2
        ) if early else 0.0,
        "average_max_gain_1_3": round(
            mean_safe(x["max_gain_1_3"] for x in samples), 4
        ) if samples else 0.0,
        "average_drawdown_1_5": round(
            mean_safe(x["max_drawdown"] for x in samples), 4
        ) if samples else 0.0,
        "status": "OK",
        "examples": samples[-20:],
    }


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "version": "V33",
        "engine": "Erken Kıvrım Motoru",
        "interval": INTERVAL,
        "scan_interval_seconds": SCAN_INTERVAL,
        "closed_candle_only": True,
        "telegram_enabled": notifier.enabled,
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "V33"})


@app.route("/api/test/<symbol>")
def api_test(symbol):
    try:
        return jsonify({"status": "ok", "result": analyze_symbol(symbol)})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/scan")
def api_scan():
    try:
        return jsonify({"status": "ok", "result": scan_once()})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


def scanner_loop():
    print("[V33 WORKER] Tarama başlıyor...", flush=True)

    while True:
        started = time.time()

        try:
            scan_once()
        except Exception as exc:
            print(f"[V33 WORKER] Hata: {exc!r}", flush=True)

        wait = max(10, SCAN_INTERVAL - (time.time() - started))
        time.sleep(wait)


def start_scanner():
    global scanner_started

    with scanner_lock:
        if scanner_started:
            return
        scanner_started = True

    threading.Thread(target=scanner_loop, daemon=True).start()


start_scanner()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
