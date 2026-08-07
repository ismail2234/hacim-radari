
import time
import requests
import pandas as pd
from flask import Flask
from threading import Thread
import os

# ================= AYARLAR =================
TELEGRAM_BOT_TOKEN = "8740764565:AAFwW-VRxTQQ_K0XFHtlwFteYGbefV0sjJM"
TELEGRAM_CHAT_ID = "937967050"

MIN_VOLUME_TRY = 100000 
SCAN_INTERVAL = 300      

sent_signals = {}

# ================= FLASK =================
app = Flask(__name__)
@app.route("/")
def home():
    return "Balina Radarı Aktif!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ================= TELEGRAM =================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=data, timeout=10)
    except: pass

# ================= BINANCE API =================
def get_tickers():
    try:
        return requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10).json()
    except: return []

def get_klines(symbol, interval="15m", limit=100):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        return requests.get("https://api.binance.com/api/v3/klines", params=params, timeout=10).json()
    except: return []

# ================= TEKNİK ANALİZ =================
def calculate_rsi(closes):
    series = pd.Series(closes)
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    return float((100 - (100 / (1 + rs))).iloc[-1])

def analyze_coin(symbol):
    candles = get_klines(symbol, interval="15m", limit=60)
    if not candles or len(candles) < 50: return None
    
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[7]) for c in candles]
    taker_buys = [float(c[9]) for c in candles]
    
    rsi = calculate_rsi(closes)
    vol_power = (volumes[-2] / (sum(volumes[-21:-2])/19)) * 100
        buy_pressure = ((taker_buys[-2] / volumes[-2]) * 100) if len(volumes) >= 2 and volumes[-2] > 0 else 0

    
    
    score = 0
    if 40 < rsi < 65: score += 25
    if vol_power > 150: score += 30
    if buy_pressure > 60: score += 30
    
    if score >= 75:
        return {
            "type": "🔥 GÜÇLÜ BALİNA SİNYALİ" if score >= 80 else "🟢 HACİM ARTIŞI",
            "rsi": round(rsi, 1),
            "vol": round(vol_power, 0),
            "pressure": round(buy_pressure, 1),
            "price": closes[-2],
            "score": score
        }
    return None

# ================= DÖNGÜ =================
def scanner():
    while True:
        tickers = get_tickers()
        for coin in tickers:
            symbol = coin["symbol"]
            if not symbol.endswith("TRY") or float(coin["quoteVolume"]) < MIN_VOLUME_TRY: continue
            
            res = analyze_coin(symbol)
            if res:
                if symbol in sent_signals and (time.time() - sent_signals[symbol] < 10800): continue
                sent_signals[symbol] = time.time()
                msg = f"{res['type']}\n\n🪙 #{symbol}\n💰 Fiyat: {res['price']} TRY\n🎯 Puan: {res['score']}/100\n🐋 Baskı: %{res['pressure']}\n🔥 Hacim: %{res['vol']}\n📊 RSI: {res['rsi']}"
                send_telegram(msg)
                time.sleep(2)
        time.sleep(300)

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    scanner()
