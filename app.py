import os
import time
import math
import threading
from datetime import datetime, timezone
from statistics import mean
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ============================================================
# V31 ERKEN AL RADARI - Binance TR
# Amaç: hareket başladıktan sonra değil, mümkünse 1-3 mum
# öncesindeki "dip -> kıvrım -> yapı -> doğrulama" bölgesini yakalamak.
# ============================================================

BASE_API = "https://www.binance.tr"
INTERVAL = os.getenv("INTERVAL", "5m")
KLINE_LIMIT = int(os.getenv("KLINE_LIMIT", "300"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))
MIN_SCORE = float(os.getenv("MIN_SCORE", "75"))
EARLY_BUY_SCORE = float(os.getenv("EARLY_BUY_SCORE", "82"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "V31-Erken-Al-Radari/1.0"})

# Aynı alarmı tekrar tekrar göndermeyi önler.
last_alert = {}
scanner_started = False
scanner_lock = threading.Lock()


def clean_symbol(symbol):
    return symbol.replace("_", "").upper()


def tr_symbol(symbol):
    s = symbol.upper()
    if "_" in s:
        return s
    if s.endswith("TRY"):
        return s[:-3] + "_TRY"
    return s


def get_klines(symbol, limit=KLINE_LIMIT):
    """Binance TR ana piyasa kline endpoint'i."""
    sym = clean_symbol(symbol)
    url = f"{BASE_API}/api/v1/klines"
    r = SESSION.get(url, params={
        "symbol": sym,
        "interval": INTERVAL,
        "limit": min(int(limit), 1000)
    }, timeout=15)
    r.raise_for_status()
    payload = r.json()

    # Binance TR dokümanında cevap data altında geliyor.
    if isinstance(payload, dict):
        if payload.get("code", 0) not in (0, "0"):
            raise RuntimeError(payload.get("msg", "Kline API error"))
        data = payload.get("data", [])
    else:
        data = payload

    rows = []
    for x in data:
        if len(x) < 10:
            continue
        rows.append({
            "time": int(x[0]),
            "open": float(x[1]),
            "high": float(x[2]),
            "low": float(x[3]),
            "close": float(x[4]),
            "volume": float(x[5]),
            "quote_volume": float(x[7]),
        })

    if len(rows) < 60:
        raise RuntimeError(f"{symbol}: yeterli mum yok ({len(rows)})")
    return rows


def sma(values, n):
    if len(values) < n:
        return mean(values)
    return mean(values[-n:])


def ema_series(values, period):
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0
    gains = []
    losses = []
    for i in range(len(values) - period, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = mean(gains)
    al = mean(losses)
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def pct(a, b):
    if b == 0:
        return 0.0
    return (a / b - 1.0) * 100.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def linear_slope(values):
    n = len(values)
    if n < 2:
        return 0.0
    xmean = (n - 1) / 2.0
    ymean = mean(values)
    den = sum((i - xmean) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return sum((i - xmean) * (y - ymean) for i, y in enumerate(values)) / den


def score_signal(candles, i):
    """
    V31 ağırlıkları:
      erken kıvrım 30
      dip          25
      yapı         20
      hacim        15
      momentum     10
    Toplam         100
    """

    if i < 55 or i >= len(candles):
        return None

    c = candles[:i + 1]
    closes = [x["close"] for x in c]
    highs = [x["high"] for x in c]
    lows = [x["low"] for x in c]
    vols = [x["volume"] for x in c]

    p = closes[-1]
    if p <= 0:
        return None

    # -------------------------
    # 1) ERKEN KIVRIM / 30
    # -------------------------
    w12 = closes[-12:]
    w6 = closes[-6:]
    slope12 = pct(w12[-1], w12[0])
    slope6 = pct(w6[-1], w6[0])

    # Önceki bölüm düşüyor, son bölüm düşüşü yavaşlatıyor veya yukarı dönüyor.
    prev6 = closes[-12:-6]
    prev_slope6 = pct(prev6[-1], prev6[0])
    turn_strength = slope6 - prev_slope6

    early = 0.0
    if prev_slope6 < -0.20:
        early += 8
    if turn_strength > 0.15:
        early += 8
    if slope6 > -0.15:
        early += 5
    if slope6 > 0.05:
        early += 4

    # Son 3 mumda çok sert kopuş varsa bu artık "erken" değildir.
    last3 = pct(closes[-1], closes[-4])
    if last3 > 1.8:
        early -= 7
    elif last3 > 1.0:
        early -= 3
    early = clamp(early, 0, 30)

    # -------------------------
    # 2) DİP / 25
    # -------------------------
    look = closes[-50:]
    lo50 = min(look)
    hi50 = max(look)
    rng = hi50 - lo50

    if rng <= 0:
        pos = 0.5
    else:
        pos = (p - lo50) / rng

    dip = 0.0
    # Dipte veya dipten yeni kalkışta daha yüksek.
    if pos <= 0.25:
        dip += 17
    elif pos <= 0.38:
        dip += 14
    elif pos <= 0.52:
        dip += 9
    elif pos <= 0.65:
        dip += 4

    r = rsi(closes, 14)
    if 35 <= r <= 50:
        dip += 6
    elif 50 < r <= 58:
        dip += 3
    elif r > 68:
        dip -= 5

    # Son dip, daha önceki dipten belirgin şekilde daha yüksekse
    # "dipten kalkış" doğrulaması.
    recent_low = min(lows[-8:])
    older_low = min(lows[-24:-8])
    if recent_low >= older_low * 0.995:
        dip += 2

    dip = clamp(dip, 0, 25)

    # -------------------------
    # 3) YAPI / 20
    # -------------------------
    structure = 0.0
    e9 = ema_series(closes, 9)
    e21 = ema_series(closes, 21)

    if e9[-1] > e21[-1]:
        structure += 6
    if e9[-1] >= e9[-3]:
        structure += 4
    if e21[-1] >= e21[-5]:
        structure += 3

    # Son 6 mumun dipleri yukarı doğru.
    l1 = min(lows[-6:-3])
    l2 = min(lows[-3:])
    if l2 >= l1 * 0.998:
        structure += 4

    # Son kapanış kısa EMA'nın üstünde.
    if p >= e9[-1] * 0.999:
        structure += 3

    structure = clamp(structure, 0, 20)

    # -------------------------
    # 4) HACİM / 15
    # -------------------------
    avg20 = mean(vols[-21:-1])
    vr = vols[-1] / avg20 if avg20 > 0 else 0.0

    volume = 0.0
    if vr >= 1.25:
        volume += 7
    elif vr >= 1.10:
        volume += 5
    elif vr >= 0.90:
        volume += 3

    # Hacim artışı birkaç mumdur devam ediyorsa daha sağlıklı.
    if len(vols) >= 4:
        v1 = mean(vols[-4:-2])
        v2 = mean(vols[-2:])
        if v2 > v1 * 1.05:
            volume += 4

    # Aşırı tek mum spike'ı: hacim puanını artırmak yerine sınırla.
    spike = vr >= 3.0
    if spike:
        volume -= 2

    volume = clamp(volume, 0, 15)

    # -------------------------
    # 5) MOMENTUM / 10
    # -------------------------
    momentum = 0.0
    ret3 = pct(closes[-1], closes[-4])
    ret6 = pct(closes[-1], closes[-7])

    if ret3 > 0:
        momentum += 4
    if ret3 > 0.20:
        momentum += 2
    if ret6 > -0.20:
        momentum += 2
    if ret6 > 0:
        momentum += 2
    momentum = clamp(momentum, 0, 10)

    # -------------------------
    # CEZALAR
    # -------------------------
    late_penalty = 0.0
    fakeout_penalty = 0.0
    breakout_penalty = 0.0
    divergence_penalty = 0.0

    # Geç hareket: fiyat zaten kısa sürede fazla yükseldiyse.
    if ret6 > 3.0:
        late_penalty = 8.0
    elif ret6 > 2.0:
        late_penalty = 4.0

    # Spike + sert fiyat hareketi = fakeout riski.
    if spike and ret3 > 1.2:
        fakeout_penalty = 5.0

    # 50 mum zirvesine çok yaklaşmışsa erken dip sinyali değildir.
    if hi50 > 0 and p >= hi50 * 0.985:
        breakout_penalty = 6.0

    # Basit negatif diverjans: fiyat yükselirken RSI son bölümde güç kaybediyorsa.
    r_now = rsi(closes, 14)
    r_prev = rsi(closes[:-5], 14)
    if pct(p, closes[-6]) > 0.8 and r_now + 4 < r_prev:
        divergence_penalty = 4.0

    total = clamp(
        early + dip + structure + volume + momentum
        - late_penalty - fakeout_penalty
        - breakout_penalty - divergence_penalty,
        0, 100
    )

    if total >= EARLY_BUY_SCORE and early >= 20 and dip >= 15 and structure >= 12:
        status = "EARLY_BUY"
    elif total >= MIN_SCORE and early >= 17 and dip >= 10:
        status = "STRONG_WATCH"
    else:
        status = "WATCH"

    return {
        "score": round(total, 2),
        "status": status,
        "early_curve_score": round(early, 2),
        "dip_score": round(dip, 2),
        "structure_score": round(structure, 2),
        "volume_score": round(volume, 2),
        "momentum_score": round(momentum, 2),
        "late_penalty": round(late_penalty, 2),
        "fakeout_penalty": round(fakeout_penalty, 2),
        "breakout_penalty": round(breakout_penalty, 2),
        "divergence_penalty": round(divergence_penalty, 2),
        "price": p,
        "volume_ratio": round(vr, 3),
        "rsi": round(r, 2),
        "return_3": round(ret3, 3),
        "return_6": round(ret6, 3),
        "time": datetime.fromtimestamp(
            candles[i]["time"] / 1000, tz=timezone.utc
        ).isoformat()
    }


def backtest(symbol, limit=KLINE_LIMIT):
    candles = get_klines(symbol, limit)
    signals = []

    # Son 12 mumu değerlendirmeye almayız; gelecekteki 5 mumun
    # gerçekten mevcut olması gerekir.
    last_signal_index = len(candles) - 6

    for i in range(55, last_signal_index):
        s = score_signal(candles, i)
        if not s:
            continue

        if s["score"] < MIN_SCORE:
            continue

        entry = candles[i]["close"]
        future = candles[i + 1:i + 6]

        if not future:
            continue

        max_gain = max(pct(x["high"], entry) for x in future)
        max_drawdown = min(pct(x["low"], entry) for x in future)

        ret1 = pct(candles[i + 1]["close"], entry)
        ret3 = pct(candles[i + 3]["close"], entry) if len(candles) >= i + 4 else 0
        ret5 = pct(candles[i + 5]["close"], entry) if len(candles) >= i + 6 else 0

        signals.append({
            "index": i,
            **s,
            "max_gain": round(max_gain, 3),
            "max_drawdown": round(max_drawdown, 3),
            "return_1": round(ret1, 3),
            "return_3": round(ret3, 3),
            "return_5": round(ret5, 3),
        })

    if not signals:
        return {
            "symbol": tr_symbol(symbol),
            "signals": 0,
            "success_rate_3": 0,
            "success_rate_5": 0,
            "average_max_gain": 0,
            "average_max_drawdown": 0,
            "average_return_1": 0,
            "average_return_3": 0,
            "average_return_5": 0,
            "average_return_10": 0,
            "signal_details": [],
            "status": "ok"
        }

    success3 = sum(1 for s in signals if s["return_3"] > 0)
    success5 = sum(1 for s in signals if s["return_5"] > 0)

    # 10 mum sonucu ayrıca hesapla.
    for s in signals:
        i = s["index"]
        entry = candles[i]["close"]
        if i + 10 < len(candles):
            s["return_10"] = round(
                pct(candles[i + 10]["close"], entry), 3
            )
        else:
            s["return_10"] = 0.0

    return {
        "symbol": tr_symbol(symbol),
        "signals": len(signals),
        "success_rate_3": round(success3 / len(signals) * 100, 2),
        "success_rate_5": round(success5 / len(signals) * 100, 2),
        "average_max_gain": round(mean(s["max_gain"] for s in signals), 3),
        "average_max_drawdown": round(mean(s["max_drawdown"] for s in signals), 3),
        "average_return_1": round(mean(s["return_1"] for s in signals), 3),
        "average_return_3": round(mean(s["return_3"] for s in signals), 3),
        "average_return_5": round(mean(s["return_5"] for s in signals), 3),
        "average_return_10": round(mean(s["return_10"] for s in signals), 3),
        "signal_details": signals[-20:],
        "status": "ok"
    }


def get_try_symbols():
    url = f"{BASE_API}/open/v1/common/symbols"
    r = SESSION.get(url, timeout=20)
    r.raise_for_status()
    p = r.json()
    data = p.get("data", {})
    items = data.get("list", []) if isinstance(data, dict) else data

    result = []
    for x in items:
        symbol = str(x.get("symbol", ""))
        quote = str(x.get("quoteAsset", ""))
        trading = int(x.get("spotTradingEnable", 1))
        if quote == "TRY" and trading:
            result.append(symbol)

    return result


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = SESSION.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text
        }, timeout=15)
        return r.ok
    except Exception:
        return False


def format_alert(symbol, s):
    return (
        "🐋 V31 ERKEN AL ADAYI\n\n"
        f"🪙 #{tr_symbol(symbol)}\n"
        f"💰 Fiyat: {s['price']:.8f}\n\n"
        f"🎯 Skor: {s['score']:.1f}/100\n"
        f"📌 Durum: {s['status']}\n\n"
        f"〰️ Erken kıvrım: {s['early_curve_score']:.1f}/30\n"
        f"📍 Dip: {s['dip_score']:.1f}/25\n"
        f"🏗️ Yapı: {s['structure_score']:.1f}/20\n"
        f"📊 Hacim: {s['volume_score']:.1f}/15\n"
        f"🚀 Momentum: {s['momentum_score']:.1f}/10\n"
        f"⏰ Geç hareket cezası: -{s['late_penalty']:.1f}\n"
        f"⚠️ Spike cezası: -{s['fakeout_penalty']:.1f}\n"
        f"📉 Kırılım cezası: -{s['breakout_penalty']:.1f}\n"
        f"🔀 Diverjans cezası: -{s['divergence_penalty']:.1f}\n\n"
        "⚠️ Yatırım tavsiyesi değildir."
    )


def scan_once():
    found = []
    try:
        symbols = get_try_symbols()
    except Exception as e:
        print("[V31] Symbol listesi alınamadı:", e, flush=True)
        return found

    for symbol in symbols:
        try:
            candles = get_klines(symbol, min(KLINE_LIMIT, 180))
            s = score_signal(candles, len(candles) - 1)
            if not s:
                continue

            if s["status"] not in ("EARLY_BUY", "STRONG_WATCH"):
                continue

            # Aynı sembol + aynı mum için tekrar alarm yok.
            key = (symbol, s["time"])
            if key in last_alert:
                continue

            # EARLY_BUY daha yüksek öncelik; STRONG_WATCH da gönderilir.
            text = format_alert(symbol, s)
            print(text, flush=True)
            send_telegram(text)
            last_alert[key] = time.time()
            found.append({"symbol": symbol, **s})

        except Exception as e:
            print(f"[V31] {symbol}: {e}", flush=True)

    # Belleğin gereksiz büyümesini engelle.
    if len(last_alert) > 2000:
        old = sorted(last_alert.items(), key=lambda z: z[1])
        for k, _ in old[:1000]:
            last_alert.pop(k, None)

    return found


def scanner_loop():
    global scanner_started
    with scanner_lock:
        if scanner_started:
            return
        scanner_started = True

    print(
        f"[V31 WORKER] {datetime.now(timezone.utc).isoformat()} UTC | "
        "Piyasa taraması başlıyor...",
        flush=True
    )

    while True:
        started = time.time()
        try:
            found = scan_once()
            print(
                f"[V31 WORKER] Tarama tamamlandı | aday={len(found)}",
                flush=True
            )
        except Exception as e:
            print("[V31 WORKER] Genel hata:", repr(e), flush=True)

        elapsed = time.time() - started
        time.sleep(max(10, SCAN_INTERVAL - elapsed))


def start_scanner():
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "version": "V31",
        "message": "V31 Erken Al Radarı çalışıyor",
        "interval": INTERVAL,
        "scan_interval_seconds": SCAN_INTERVAL
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "V31"})


@app.route("/api/backtest/<symbol>")
def api_backtest(symbol):
    try:
        return jsonify({"result": backtest(symbol), "status": "ok"})
    except Exception as e:
        return jsonify({
            "result": None,
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/api/scan")
def api_scan():
    try:
        return jsonify({"result": scan_once(), "status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# Gunicorn import ettiğinde worker başlar.
start_scanner()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
