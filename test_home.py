import os
import time
import requests
from datetime import datetime, timezone

SYMBOL = "HOME_TRY"
BASE_API = "https://api.binance.me"

INTERVAL = "5m"
LIMIT = 200
SCAN_SECONDS = 300

MIN_VOLUME_TRY = 10000

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "HOME-Early-Test/1.0",
    "Accept": "application/json",
})

last_state = None
entry_price = None


def pct(a, b):
    if b == 0:
        return 0.0
    return (a / b - 1.0) * 100.0


def mean_safe(values):
    # Performance optimization: sum(xs) / len(xs) avoids statistics.mean overhead (~40x faster in Python 3.12)
    if not values:
        return 0.0
    if not isinstance(values, (list, tuple)):
        values = list(values)
        if not values:
            return 0.0
    return sum(values) / len(values)


def ema(values, period):
    if not values:
        return 0.0

    k = 2.0 / (period + 1.0)
    result = values[0]

    for value in values[1:]:
        result = value * k + result * (1.0 - k)

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = mean_safe(gains)
    avg_loss = mean_safe(losses)

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss

    return 100.0 - (100.0 / (1.0 + rs))


def get_klines():
    response = SESSION.get(
        f"{BASE_API}/api/v1/klines",
        params={
            "symbol": SYMBOL.replace("_", ""),
            "interval": INTERVAL,
            "limit": LIMIT,
        },
        timeout=20,
    )

    response.raise_for_status()

    payload = response.json()

    if isinstance(payload, dict):
        data = payload.get("data", [])
    else:
        data = payload

    candles = []

    for row in data:
        if len(row) < 10:
            continue

        candles.append({
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "quote_volume": float(row[7]),
            "close_time": int(row[6]),
        })

    if len(candles) < 60:
        raise RuntimeError(
            f"Yeterli mum yok: {len(candles)}"
        )

    # Açık mumu kullanma.
    now_ms = int(time.time() * 1000)

    if candles[-1]["close_time"] >= now_ms:
        candles = candles[:-1]

    return candles


def analyze(candles):

    closes = [x["close"] for x in candles]
    lows = [x["low"] for x in candles]
    highs = [x["high"] for x in candles]
    volumes = [x["quote_volume"] for x in candles]

    price = closes[-1]

    ret1 = pct(closes[-1], closes[-2])
    ret3 = pct(closes[-1], closes[-4])
    ret6 = pct(closes[-1], closes[-7])

    previous6 = pct(
        closes[-7],
        closes[-13]
    )

    turn = ret6 - previous6

    low50 = min(closes[-50:])
    high50 = max(closes[-50:])

    position = 0.5

    if high50 > low50:
        position = (
            (price - low50)
            /
            (high50 - low50)
        )

    average_volume = mean_safe(
        volumes[-21:-1]
    )

    volume_ratio = (
        volumes[-1] / average_volume
        if average_volume > 0
        else 0
    )

    ema9 = ema(closes[-60:], 9)
    ema21 = ema(closes[-60:], 21)

    r = rsi(closes, 14)

    recent_low = min(lows[-8:])
    old_low = min(lows[-24:-8])

    higher_low = (
        recent_low >= old_low * 0.998
    )

    score = 0

    # DİP
    if position <= 0.30:
        score += 20
    elif position <= 0.45:
        score += 14
    elif position <= 0.60:
        score += 7

    # KIVRIM
    if previous6 < -0.30:
        score += 10

    if turn > 0.20:
        score += 15
    elif turn > 0.10:
        score += 8

    # HACİM
    if volume_ratio >= 1.20:
        score += 15
    elif volume_ratio >= 1.05:
        score += 8

    # RSI
    if 35 <= r <= 55:
        score += 10

    # HIGHER LOW
    if higher_low:
        score += 10

    # EMA
    if ema9 >= ema21:
        score += 10

    # Erkenlik
    if ret3 <= 0.80:
        score += 10

    # Geç hareket cezası
    if ret3 > 1.50:
        score -= 15

    if ret6 > 3.00:
        score -= 15

    score = max(0, min(100, score))

    # AL
    if (
        score >= 75
        and
        volume_ratio >= 1.05
        and
        ret3 <= 1.50
    ):
        state = "AL"

    # SAT
    elif (
        entry_price is not None
        and
        price > entry_price * 1.02
        and
        (
            ret1 < -0.60
            or
            r > 70
            or
            ema9 < ema21
        )
    ):
        state = "SAT"

    else:
        state = "BEKLE"

    return {
        "state": state,
        "score": score,
        "price": price,
        "rsi": r,
        "volume_ratio": volume_ratio,
        "ret1": ret1,
        "ret3": ret3,
        "ret6": ret6,
        "position": position,
        "higher_low": higher_low,
    }


def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "[TELEGRAM] BOT TOKEN YOK",
            flush=True
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM] CHAT ID YOK",
            flush=True
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    try:

        response = SESSION.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=15,
        )

        response.raise_for_status()

        result = response.json()

        if result.get("ok"):
            print(
                "[TELEGRAM] Mesaj gönderildi",
                flush=True
            )
            return True

        print(
            f"[TELEGRAM] API HATASI: {result}",
            flush=True
        )

        return False

    except Exception as exc:

        print(
            f"[TELEGRAM] HATA: {exc}",
            flush=True
        )

        return False
def format_message(result):

    state = result["state"]
    price = result["price"]
    score = result["score"]

    if state == "AL":
        title = "🟢 HOME/TRY AL SİNYALİ"
    elif state == "SAT":
        title = "🔴 HOME/TRY SAT SİNYALİ"
    else:
        title = "🟡 HOME/TRY TAKİP"

    return (
        f"{title}\n\n"
        f"🪙 #HOME_TRY\n"
        f"💰 Fiyat: {price:.8f} TL\n\n"
        f"🎯 Skor: {score}/100\n"
        f"📊 Hacim: {result['volume_ratio']:.2f}x\n"
        f"📈 Son 1 mum: {result['ret1']:+.2f}%\n"
        f"📈 Son 3 mum: {result['ret3']:+.2f}%\n"
        f"📈 Son 6 mum: {result['ret6']:+.2f}%\n"
        f"📊 RSI: {result['rsi']:.1f}\n"
        f"📍 50 mum konumu: "
        f"{result['position'] * 100:.1f}%\n"
        f"🏗️ Higher Low: "
        f"{'EVET' if result['higher_low'] else 'HAYIR'}\n\n"
        "🕯️ Sadece kapanmış mum kullanıldı.\n"
        "⚠️ Test sistemidir, yatırım tavsiyesi değildir."
    )


def run():

    global last_state
    global entry_price

    print(
        "🎯 HOME/TRY TEST RADARI BAŞLADI",
        flush=True
    )

    while True:

        try:

            candles = get_klines()

            result = analyze(candles)

            state = result["state"]

            print(
                f"[HOME V40] "
                f"{state} | "
                f"Skor={result['score']} | "
                f"Fiyat={result['price']} | "
                f"RSI={result['rsi']:.1f} | "
                f"Vol={result['volume_ratio']:.2f}x",
                flush=True
            )

            # Sadece durum değiştiğinde Telegram gönder.
            if state != last_state:

                # AL gerçekleşirse giriş fiyatını kaydet.
                if state == "AL":

                    entry_price = result["price"]

                # SAT gerçekleşirse pozisyonu kapat.
                elif state == "SAT":

                    entry_price = None

                message = format_message(result)

                send_telegram(message)

                last_state = state

        except Exception as exc:

            print(
                f"[HOME V40] HATA: {exc}",
                flush=True
            )

        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    run()
