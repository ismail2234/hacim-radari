
import time
import requests
import pandas as pd
from flask import Flask
from threading import Thread
import os

# ================= AYARLAR =================
TELEGRAM_BOT_TOKEN = "8740764565:AAFwW-VRxTQQ_K0XFHtlwFteYGbefV0sjJM"
TELEGRAM_CHAT_ID = "937967050"

MIN_VOLUME_TRY = 100000  # Hacimsiz coinleri elemeli (Minimum 100.000 TRY)
SCAN_INTERVAL = 300      # 5 dakikada bir tarama

sent_signals = {}

# ================= FLASK (RAILWAY 7/24 KORUMASI) =================
app = Flask(__name__)

@app.route("/")
def home():
    return "Gelişmiş Balina & Hacim Radarı Aktif!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ================= TELEGRAM =================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print("Telegram hata:", e)

# ================= BINANCE API =================
def get_tickers():
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        r = requests.get(url, timeout=10)
        return r.json()
    except Exception as e:
        print("Ticker hata:", e)
        return []

def get_klines(symbol, interval="15m", limit=100):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=10)
        return r.json()
    except Exception:
        return []

# ================= GLOBAL BTC TREND KONTROLÜ =================
def is_btc_safe():
    """
    Bitcoin genel trendi çökerken altcoinlerde sahte yükselişe aldanmamak için
    BTCUSDT 1 saatlik trendin ortalama üzerinde olup olmadığını kontrol eder.
    """
    candles = get_klines("BTCUSDT", interval="1h", limit=30)
    if not candles or len(candles) < 25:
        return True # Veri alınamazsa engelleme yapma
    
    closes = [float(c[4]) for c in candles]
    sma20 = sum(closes[-20:]) / 20
    current_btc = closes[-1]
    
    return current_btc >= (sma20 * 0.995) # BTC ortalamanın %0.5 altından fazla düşmediyse güvenli

# ================= TEKNİK İNDİKATÖRLER =================
def calculate_rsi(closes, period=14):
    series = pd.Series(closes)
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def calculate_macd(closes):
    def ema(data, period):
        k = 2 / (period + 1)
        ema_val = data[0]
        for price in data[1:]:
            ema_val = (price * k) + (ema_val * (1 - k))
        return ema_val

    ema12 = ema(closes[-50:], 12)
    ema26 = ema(closes[-50:], 26)
    macd_value = ema12 - ema26
    return "YUKARI" if macd_value > 0 else "ZAYIF"

# ================= BALİNA & ERKEN PATLAMA ANALİZİ =================
def analyze_coin(symbol):
    candles = get_klines(symbol, interval="15m", limit=100)
    if not candles or len(candles) < 50:
        return None

    closes = [float(c[4]) for c in candles]
    total_volumes = [float(c[7]) for c in candles]       # Toplam TRY hacmi
    taker_buy_volumes = [float(c[9]) for c in candles]   # Balina/Agresif alıcı TRY hacmi

    rsi = calculate_rsi(closes)
    macd = calculate_macd(closes)

    # 1. HACİM ANOMALİSİ (Hacim patlaması var mı?)
    last_volume = total_volumes[-2] # Kapanmış son mum
    avg_volume = sum(total_volumes[-21:-2]) / 19
    volume_power = (last_volume / avg_volume) * 100 if avg_volume > 0 else 0

    # 2. BALİNA ALIM BASKISI (Taker Buy Ratio)
    # Satışları kim agresifçe yiyor? %60 üzeri balina alımı demektir.
    last_taker_buy = taker_buy_volumes[-2]
    buy_pressure = (last_taker_buy / last_volume) * 100 if last_volume > 0 else 0

    # ================= PUANLAMA ALGORİTMASI =================
    score = 0
    
    # RSI: Henüz aşırı alıma girmemiş ama momentum başlamış (42 - 64 arası)
    if 42 <= rsi <= 64:
        score += 25
    
    # MACD: Trend yukarı yönlü
    if macd == "YUKARI":
        score += 20
        
    # Hacim Gücü: Son mum ortalamanın en az 1.8 katına (%180+) çıkmışsa
    if volume_power >= 180:
        score += 30
    elif volume_power >= 140:
        score += 15

    # Balina Alım Baskısı: Alıcılar %58'den fazlaysa
    if buy_pressure >= 65:
        score += 25
    elif buy_pressure >= 58:
        score += 15

    # ================= SİNYAL KARARI =================
    # Toplam puan 70 veya üzerindeyse sinyal gönderilir
    if score >= 70:
        signal_type = "🔥 GÜÇLÜ AL (BALİNA GİRİŞİ)" if (score >= 85 and buy_pressure >= 62) else "🟢 AL (HACİM ARTIŞI)"
        return {
            "type": signal_type,
            "rsi": round(rsi, 1),
            "macd": macd,
            "volume_power": round(volume_power, 0),
            "buy_pressure": round(buy_pressure, 1),
            "price": closes[-2],
            "score": score
        }

    return None

# ================= TARAMA DÖNGÜSÜ =================
def scanner():
    print("🚀 Gelişmiş Balina & Hacim Radarı başladı...")
    send_telegram("🔔 *Yapay Zeka Destekli Balina Radarı Aktif!*\nErken hacim patlamaları ve balina alım baskısı takibe alındı.")

    while True:
        try:
            # Önce genel piyasa (BTC) güvenli mi kontrol et
            btc_safe = is_btc_safe()
            if not btc_safe:
                print("⚠️ BTC trendi zayıf, sahte kırılım riskine karşı filtre devrede.")

            tickers = get_tickers()
            now = time.time()

            for coin in tickers:
                symbol = coin["symbol"]

                if not symbol.endswith("TRY"):
                    continue

                volume = float(coin["quoteVolume"])
                if volume < MIN_VOLUME_TRY:
                    continue

                # Aynı coine 2.5 saatte (9000 sn) bir defadan fazla sinyal atma
                if symbol in sent_signals and (now - sent_signals[symbol] < 9000):
                    continue

                result = analyze_coin(symbol)

                if result:
                    # Eğer BTC düşüyorsa ve sinyal "GÜÇLÜ AL" değilse riske girme
                    if not btc_safe and "GÜÇLÜ" not in result["type"]:
                        continue

                    sent_signals[symbol] = now

                    message = (
                        f"{result['type']}\n\n"
                        f"🪙 *Coin:* #{symbol}\n"
                        f"💰 *Fiyat:* `{result['price']} TRY`\n"
                        f"🎯 *Sinyal Puanı:* `{result['score']} / 100`\n\n"
                        f"🐋 *Balina Alım Baskısı:* `% {result['buy_pressure']}`\n"
                        f"🔥 *Hacim Patlaması:* `% {result['volume_power']}`\n"
                        f"📊 *RSI:* `{result['rsi']}` | *MACD:* `{result['macd']}`\n\n"
                        f"💡 _Not: Balina alım baskısının %60 üzerinde olması, tahtadaki satıcıların agresifçe süpürüldüğünü gösterir._"
                    )

                    send_telegram(message)
                    time.sleep(2)

            print("✅ Tarama turu tamamlandı. Bir sonraki tur bekleniyor...")
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("Tarama hatası:", e)
            time.sleep(20)

# ================= ANA BAŞLANGIÇ =================
if __name__ == "__main__":
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    scanner()
  
