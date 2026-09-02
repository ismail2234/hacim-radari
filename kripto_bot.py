"""
Kripto AL-Sinyali Tarayıcı Bot (Binance TR / BtcTurk altyapısı, TRY paritesi)
================================================================================
Binance TR, teknik altyapı olarak BtcTurk borsasını kullanır ve ccxt
kütüphanesi bunu "btcturk" borsa id'siyle destekler. Bu bot TRY
paritelerini (BTC_TRY, ETH_TRY, vb.) doğrudan bu borsadan tarar.

Sadece "gerçekten güçlü görünen" coinleri, üç kriteri BİRDEN
sağladığında Telegram'a AL sinyali olarak bildirir:

  1) TREND    -> Kısa MA, Uzun MA'yı yukarı kesmiş (yükselişe dönmüş)
  2) MOMENTUM -> RSI 50'nin üzerinde ve yükseliyor (aşırı alım bölgesinde değil)
  3) HACİM    -> Son mumun hacmi, ortalama hacmin üzerinde (gerçek ilgi var)

Üçü birden sağlanmadan sinyal ATILMAZ. Bu yüzden bazı turlarda hiç
sinyal gelmeyebilir — bu normaldir.

SAT sinyali YOKTUR. Bu sadece bir "fırsat tarayıcı"dır, gerçek para
kullanmaz, otomatik işlem açmaz.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY     (varsayılan: TRY)
    TIMEFRAME          (varsayılan: 15m)
    SHORT_WINDOW       (varsayılan: 9)
    LONG_WINDOW        (varsayılan: 21)
    RSI_PERIOD         (varsayılan: 14)
    RSI_MIN            (varsayılan: 50)
    RSI_MAX            (varsayılan: 75)
    VOLUME_MULTIPLIER  (varsayılan: 1.5)
    CHECK_INTERVAL_SEC (varsayılan: 300)
    EXCLUDE_COINS      (varsayılan: "")   -> örn: "USDT,USDC"
    COOLDOWN_MIN       (varsayılan: 240)
"""

import ccxt
import pandas as pd
import time
import os
import csv
import requests
from datetime import datetime

# ---------------------- AYARLAR ----------------------
QUOTE_CURRENCY = os.getenv("QUOTE_CURRENCY", "TRY")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
SHORT_WINDOW = int(os.getenv("SHORT_WINDOW", 9))
LONG_WINDOW = int(os.getenv("LONG_WINDOW", 21))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
RSI_MIN = float(os.getenv("RSI_MIN", 50))
RSI_MAX = float(os.getenv("RSI_MAX", 75))
VOLUME_MULTIPLIER = float(os.getenv("VOLUME_MULTIPLIER", 1.5))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 300))
EXCLUDE_COINS = set(x.strip().upper() for x in os.getenv("EXCLUDE_COINS", "").split(",") if x.strip())
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", 240))
CSV_FILE = "signals.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Binance TR = BtcTurk altyapısı. ccxt bunu 'btcturk' id'siyle destekliyor.
exchange = ccxt.btcturk()

last_signal_time = {}


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")


def log_signal(symbol, price, rsi, vol_ratio):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["zaman", "coin", "fiyat", "rsi", "hacim_orani"])
        writer.writerow([now, symbol, f"{price:.6f}", f"{rsi:.1f}", f"{vol_ratio:.2f}"])


def get_symbols(quote, exclude):
    markets = exchange.load_markets()
    symbols = []
    for symbol, market in markets.items():
        if market.get("quote") != quote:
            continue
        base = market.get("base", "")
        if base in exclude:
            continue
        if not market.get("active", True):
            continue
        symbols.append(symbol)
    return symbols


def get_ohlcv(symbol, timeframe, limit):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df


def compute_rsi(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def evaluate(symbol):
    min_needed = max(LONG_WINDOW, RSI_PERIOD) + 20
    df = get_ohlcv(symbol, TIMEFRAME, limit=min_needed)
    if len(df) < min_needed:
        return None

    df["ma_short"] = df["close"].rolling(SHORT_WINDOW).mean()
    df["ma_long"] = df["close"].rolling(LONG_WINDOW).mean()
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)
    df["vol_avg"] = df["volume"].rolling(20).mean()

    prev_short, prev_long = df["ma_short"].iloc[-2], df["ma_long"].iloc[-2]
    curr_short, curr_long = df["ma_short"].iloc[-1], df["ma_long"].iloc[-1]
    curr_rsi, prev_rsi = df["rsi"].iloc[-1], df["rsi"].iloc[-2]
    curr_vol, avg_vol = df["volume"].iloc[-1], df["vol_avg"].iloc[-1]
    price = df["close"].iloc[-1]

    if pd.isna(curr_rsi) or pd.isna(avg_vol) or avg_vol == 0:
        return None

    trend_ok = prev_short <= prev_long and curr_short > curr_long
    momentum_ok = RSI_MIN <= curr_rsi <= RSI_MAX and curr_rsi > prev_rsi
    vol_ratio = curr_vol / avg_vol
    volume_ok = vol_ratio >= VOLUME_MULTIPLIER

    if trend_ok and momentum_ok and volume_ok:
        return {"price": price, "rsi": curr_rsi, "vol_ratio": vol_ratio}
    return None


def scan_once(symbols):
    hits = []
    now = time.time()

    for symbol in symbols:
        try:
            last = last_signal_time.get(symbol, 0)
            if now - last < COOLDOWN_MIN * 60:
                continue

            result = evaluate(symbol)
            if result:
                hits.append((symbol, result))
                last_signal_time[symbol] = now
                log_signal(symbol, result["price"], result["rsi"], result["vol_ratio"])

        except Exception as e:
            print(f"{symbol} taranırken hata: {e}")

    return hits


def format_and_send(hits):
    if not hits:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Kriterlere uyan coin yok.")
        return

    lines = []
    for symbol, r in hits:
        name = symbol.replace(f"/{QUOTE_CURRENCY}", "")
        lines.append(
            f"🟢 {name}  →  {r['price']:.2f} {QUOTE_CURRENCY}\n"
            f"   RSI: {r['rsi']:.1f} | Hacim: {r['vol_ratio']:.1f}x ortalama"
        )
    msg = "🚀 KRİTERLERE UYAN COIN(LER) — Binance TR\n\n" + "\n\n".join(lines)
    print(msg)
    send_telegram(msg)


def run_bot():
    symbols = get_symbols(QUOTE_CURRENCY, EXCLUDE_COINS)

    start_msg = (
        f"🤖 AL-sinyali tarayıcı bot başladı\n"
