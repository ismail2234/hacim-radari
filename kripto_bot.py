"""
Kripto Al-Sat Sinyal Botu (Paper Trading + Telegram Bildirimi)
================================================================
Strateji: Hareketli Ortalama Kesişimi (Moving Average Crossover)
- Kısa MA, Uzun MA'yı YUKARI keserse -> AL sinyali
- Kısa MA, Uzun MA'yı AŞAĞI keserse -> SAT sinyali

Bu bot GERÇEK PARA KULLANMAZ. Binance'ten canlı fiyat verisi çeker,
işlemleri simüle eder ve her sinyalde Telegram'a mesaj atar.

Railway'de "worker" olarak 7/24 çalışacak şekilde tasarlanmıştır.
Telegram bot token'ı ve chat id'si ortam değişkeni (environment variable)
olarak verilir; kod içinde hiçbir gizli bilgi yoktur.

Gerekli ortam değişkenleri (Railway > Variables kısmından eklenir):
    TELEGRAM_BOT_TOKEN   -> BotFather'dan aldığın token
    TELEGRAM_CHAT_ID     -> mesajın gideceği chat id

Opsiyonel ortam değişkenleri (verilmezse varsayılan kullanılır):
    SYMBOL            (varsayılan: BTC/USDT)
    TIMEFRAME         (varsayılan: 1m)
    SHORT_WINDOW      (varsayılan: 9)
    LONG_WINDOW       (varsayılan: 21)
    STARTING_BALANCE  (varsayılan: 1000)
    CHECK_INTERVAL_SEC (varsayılan: 30)
"""

import ccxt
import pandas as pd
import time
import os
import csv
import requests
from datetime import datetime

# ---------------------- AYARLAR (ortam değişkeninden okunur) ----------------------
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "1m")
SHORT_WINDOW = int(os.getenv("SHORT_WINDOW", 9))
LONG_WINDOW = int(os.getenv("LONG_WINDOW", 21))
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", 1000))
TRADE_AMOUNT_PCT = float(os.getenv("TRADE_AMOUNT_PCT", 1.0))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 30))
CSV_FILE = "trades.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

exchange = ccxt.binance()


def send_telegram(message):
    """Telegram'a mesaj gönderir. Token/chat id yoksa sessizce atlar (hata vermez)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")


class PaperAccount:
    """Gerçek para kullanmayan, kağıt üzerinde işlem yapan hesap."""

    def __init__(self, starting_balance):
        self.usdt = starting_balance
        self.coin = 0.0
        self.position = None

    def buy(self, price, pct=1.0):
        if self.position == "LONG":
            return
        spend = self.usdt * pct
        self.coin = spend / price
        self.usdt -= spend
        self.position = "LONG"
        self._log("AL", price)

    def sell(self, price):
        if self.position != "LONG":
            return
        proceeds = self.coin * price
        self.usdt += proceeds
        self.coin = 0.0
        self.position = None
        self._log("SAT", price)

    def equity(self, price):
        return self.usdt + self.coin * price

    def _log(self, side, price):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [now, side, f"{price:.2f}", f"{self.usdt:.2f}", f"{self.coin:.6f}"]
        line = f"[{now}] {side} {SYMBOL} @ {price:.2f} | Bakiye: {self.usdt:.2f} USDT | Coin: {self.coin:.6f}"
        print(line)

        file_exists = os.path.isfile(CSV_FILE)
        with open(CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["zaman", "islem", "fiyat", "usdt_bakiye", "coin_bakiye"])
            writer.writerow(row)

        emoji = "🟢" if side == "AL" else "🔴"
        send_telegram(f"{emoji} {side} SİNYALİ\nParite: {SYMBOL}\nFiyat: {price:.2f}\nBakiye: {self.usdt:.2f} USDT")


def get_ohlcv(symbol, timeframe, limit=100):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
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


def run_bot():
    account = PaperAccount(STARTING_BALANCE)
    start_msg = (
        f"🤖 Bot başladı\nParite: {SYMBOL}\nZaman dilimi: {TIMEFRAME}\n"
        f"Strateji: MA{SHORT_WINDOW}/MA{LONG_WINDOW} kesişimi\n"
        f"Başlangıç bakiyesi: {STARTING_BALANCE} USDT (simülasyon)"
    )
    print(start_msg)
    send_telegram(start_msg)

    last_error_notified = False

    while True:
        try:
            df = get_ohlcv(SYMBOL, TIMEFRAME, limit=LONG_WINDOW + 20)
            price = df["close"].iloc[-1]
            signal = compute_signal(df)

            if signal == "BUY":
                account.buy(price, pct=TRADE_AMOUNT_PCT)
            elif signal == "SELL":
                account.sell(price)

            equity = account.equity(price)
            print(f"Fiyat: {price:.2f} | Pozisyon: {account.position} | Toplam Varlık: {equity:.2f} USDT")
            last_error_notified = False

            time.sleep(CHECK_INTERVAL_SEC)

        except Exception as e:
            print(f"Hata oluştu: {e}. 10 saniye sonra tekrar denenecek...")
            if not last_error_notified:
                send_telegram(f"⚠️ Bot hata aldı: {e}")
                last_error_notified = True
            time.sleep(10)


if __name__ == "__main__":
    run_bot()
    
