"""
Kripto Çoklu-Coin Tarayıcı Bot (Sinyal + Telegram Bildirimi)
================================================================
Binance'in (global, ccxt üzerinden) USDT paritelerini tarar.
Her coin için MA kesişim stratejisiyle "yükselişe geçti" sinyali
oluşur oluşmaz Telegram'a AL sinyali gönderir. SAT sinyalleri de
aynı şekilde bildirilir. Gerçek para kullanılmaz, sadece sinyal +
sanal (paper) pozisyon takibi yapılır.

Not: Binance TR, ccxt kütüphanesinde ayrı bir borsa olarak
desteklenmiyor. Fiyat verisi global Binance'ten (aynı piyasa)
çekiliyor; bu sinyal üretimi için pratikte fark yaratmaz.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY     (varsayılan: USDT)
    TOP_N_COINS        (varsayılan: 30)  -> işlem hacmine göre en yüksek N coin taranır
    TIMEFRAME          (varsayılan: 15m)
    SHORT_WINDOW       (varsayılan: 9)
    LONG_WINDOW        (varsayılan: 21)
    CHECK_INTERVAL_SEC (varsayılan: 300)  -> her tur kaç saniyede bir taransın
    EXCLUDE_COINS      (varsayılan: "")  -> virgülle ayrılmış, taranmasın istenenler örn: "USDC,FDUSD"
"""

import ccxt
import pandas as pd
import time
import os
import csv
import requests
from datetime import datetime

# ---------------------- AYARLAR ----------------------
QUOTE_CURRENCY = os.getenv("QUOTE_CURRENCY", "USDT")
TOP_N_COINS = int(os.getenv("TOP_N_COINS", 30))
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
SHORT_WINDOW = int(os.getenv("SHORT_WINDOW", 9))
LONG_WINDOW = int(os.getenv("LONG_WINDOW", 21))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 300))
EXCLUDE_COINS = set(x.strip().upper() for x in os.getenv("EXCLUDE_COINS", "").split(",") if x.strip())
CSV_FILE = "signals.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

exchange = ccxt.binance()

# Her coin için son bilinen pozisyon durumunu tutar (aynı sinyali tekrar tekrar göndermemek için)
positions = {}


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")


def log_signal(symbol, side, price):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["zaman", "coin", "sinyal", "fiyat"])
        writer.writerow([now, symbol, side, f"{price:.6f}"])


def get_top_symbols(quote, top_n, exclude):
    """İşlem hacmine göre en yüksek N adet USDT paritesini döndürür."""
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    candidates = []
    for symbol, market in markets.items():
        if not market.get("spot", True):
            continue
        if market.get("quote") != quote:
            continue
        base = market.get("base", "")
        if base in exclude:
            continue
        ticker = tickers.get(symbol)
        if not ticker or ticker.get("quoteVolume") is None:
            continue
        candidates.append((symbol, ticker["quoteVolume"]))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates[:top_n]]


def get_ohlcv(symbol, timeframe, limit):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df


def compute_signal(df):
    df["ma_short"] = df["close"].rolling(SHORT_WINDOW).mean()
    df["ma_long"] = df["close"].rolling(LONG_WINDOW).mean()

    if len(df) < LONG_WINDOW + 1:
        return None

    prev_short, prev_long = df["ma_short"].iloc[-2], df["ma_long"].iloc[-2]
    curr_short, curr_long = df["ma_short"].iloc[-1], df["ma_long"].iloc[-1]

    if prev_short <= prev_long and curr_short > curr_long:
        return "BUY"
    elif prev_short >= prev_long and curr_short < curr_long:
        return "SELL"
    return None


def scan_once(symbols):
    buy_hits = []
    sell_hits = []

    for symbol in symbols:
        try:
            df = get_ohlcv(symbol, TIMEFRAME, limit=LONG_WINDOW + 20)
            price = df["close"].iloc[-1]
            signal = compute_signal(df)

            if signal == "BUY" and positions.get(symbol) != "LONG":
                positions[symbol] = "LONG"
                buy_hits.append((symbol, price))
                log_signal(symbol, "AL", price)
            elif signal == "SELL" and positions.get(symbol) == "LONG":
                positions[symbol] = None
                sell_hits.append((symbol, price))
                log_signal(symbol, "SAT", price)

        except Exception as e:
            print(f"{symbol} taranırken hata: {e}")

    return buy_hits, sell_hits


def format_and_send(buy_hits, sell_hits):
    if buy_hits:
        lines = [f"🟢 {sym.replace('/USDT','')}  →  {price:.4f} USDT" for sym, price in buy_hits]
        msg = "🚀 YÜKSELİŞE GEÇEN COINLER (AL sinyali)\n\n" + "\n".join(lines)
        print(msg)
        send_telegram(msg)

    if sell_hits:
        lines = [f"🔴 {sym.replace('/USDT','')}  →  {price:.4f} USDT" for sym, price in sell_hits]
        msg = "📉 DÜŞÜŞE GEÇEN COINLER (SAT sinyali)\n\n" + "\n".join(lines)
        print(msg)
        send_telegram(msg)

    if not buy_hits and not sell_hits:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Yeni sinyal yok.")


def run_bot():
    start_msg = (
        f"🤖 Tarayıcı bot başladı\n"
        f"Kaynak: Binance (global, public veri)\n"
        f"Taranan: En yüksek hacimli {TOP_N_COINS} adet {QUOTE_CURRENCY} paritesi\n"
        f"Zaman dilimi: {TIMEFRAME} | Strateji: MA{SHORT_WINDOW}/MA{LONG_WINDOW} kesişimi\n"
        f"Tarama aralığı: {CHECK_INTERVAL_SEC} sn"
    )
    print(start_msg)
    send_telegram(start_msg)

    symbols = []
    last_symbol_refresh = 0
    SYMBOL_REFRESH_INTERVAL = 3600  # coin listesini saatte bir tazele

    while True:
        try:
            now = time.time()
            if now - last_symbol_refresh > SYMBOL_REFRESH_INTERVAL or not symbols:
                symbols = get_top_symbols(QUOTE_CURRENCY, TOP_N_COINS, EXCLUDE_COINS)
                last_symbol_refresh = now
                print(f"Taranan coin listesi güncellendi ({len(symbols)} adet).")

            buy_hits, sell_hits = scan_once(symbols)
            format_and_send(buy_hits, sell_hits)

            time.sleep(CHECK_INTERVAL_SEC)

        except Exception as e:
            print(f"Genel hata: {e}. 30 saniye sonra tekrar denenecek...")
            send_telegram(f"⚠️ Bot hata aldı: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
