
import time
import requests
import os
from flask import Flask
from threading import Thread

# ==========================================
# 1. AYARLAR VE TELEGRAM BİLGİLERİ
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("8740764565:AAFwW-VRxTQQ_K0XFHtlwFteYGbefV0sjJM)
TELEGRAM_CHAT_ID = os.environ.get(937967050)

# Profesyonel Radar Ayarları (Test için 1000$ yaptık, sonra 15000$ yapabilirsin)
MIN_VOLUME_USD = 1000  
CHECK_INTERVAL = 8      
COOLDOWN_TIME = 300     

cooldown_tracker = {}
previous_volumes = {}
previous_prices = {}

# ==========================================
# 2. FLASK WEB SUNUCUSU (RENDER İÇİN)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Hacim Radari PRO Ultimate (v2.5) Aktif ve Taramaya Devam Ediyor!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ==========================================
# 3. PROFESYONEL İNDİKATÖR (RSI HESAPLAMA)
# ==========================================
def calculate_rsi(symbol, period=14):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit={period+1}"
        response = requests.get(url, timeout=5).json()
        
        changes = []
        for i in range(1, len(response)):
            close_now = float(response[i][4])
            close_prev = float(response[i-1][4])
            changes.append(close_now - close_prev)
            
        gains = [c for c in changes if c > 0]
        losses = [abs(c) for c in changes if c < 0]
        
        avg_gain = sum(gains) / period if gains else 0.001
        avg_loss = sum(losses) / period if losses else 0.001
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 1)
    except:
        return 50.0  

def get_rsi_status(rsi):
    if rsi >= 70:
        return f"🔴 {rsi} (Aşırı Alım - Tepeden Alma!)"
    elif rsi <= 30:
        return f"🟢 {rsi} (Dip Bölgesi - Fırsat Olabilir)"
    else:
        return f"🟡 {rsi} (Normal Trend Bölgesi)"

# ==========================================
# 4. TELEGRAM MESAJ GÖNDERİCİ
# ==========================================
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram Gönderim Hatası: {e}")

# ==========================================
# 5. ANA TARAMA DÖNGÜSÜ (PRO ULTIMATE)
# ==========================================
def start_scanner():
    global previous_volumes, previous_prices, cooldown_tracker
    print("🚀 PRO Ultimate Hacim Radarı Başlatıldı...")
    
    while True:
        try:
            response = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
            data = response.json()
            
            btc_change = 0.0
            for item in data:
                if item["symbol"] == "BTCUSDT":
                    btc_change = float(item["priceChangePercent"])
                    break
            
            btc_icon = "🟢" if btc_change >= 0 else "🔴"
            current_time = time.time()
            
            for coin in data:
                symbol = coin["symbol"]
                
                if not symbol.endswith("USDT") or "BULL" in symbol or "BEAR" in symbol:
                    continue
                
                quote_volume = float(coin["quoteVolume"])
                price = float(coin["lastPrice"])
                change_24h = float(coin["priceChangePercent"])
                
                if symbol in previous_volumes and symbol in previous_prices:
                    volume_diff = quote_volume - previous_volumes[symbol]
                    price_diff = price - previous_prices[symbol]
                    
                    if volume_diff >= MIN_VOLUME_USD:
                        last_alert_time = cooldown_tracker.get(symbol, 0)
                        
                        if current_time - last_alert_time > COOLDOWN_TIME:
                            cooldown_tracker[symbol] = current_time
                            
                            if price_diff > 0:
                                direction_text = "🟢 <b>ALIM BASKISI (Para Girişi)</b>"
                            elif price_diff < 0:
                                direction_text = "🔴 <b>SATIŞ BASKISI (Dump Riski)</b>"
                            else:
                                direction_text = "🟡 <b>YATAY HACİM GİRİŞİ</b>"
                            
                            stars = "⭐⭐⭐ 🐋" if volume_diff >= 50000 else "⭐⭐"
                            
                            rsi_value = calculate_rsi(symbol)
                            rsi_text = get_rsi_status(rsi_value)
                            
                            clean_symbol = symbol.replace("USDT", "")
                            chart_url = f"https://www.binance.com/tr/trade/{clean_symbol}_USDT"
                            
                            msg = (
                                f"🔥 <b>GÜÇLÜ HACİM SİNYALİ!</b> {stars}\n\n"
                                f"🪙 <b>Coin:</b> #{symbol}\n"
                                f"⚡ <b>Yön:</b> {direction_text}\n"
                                f"💵 <b>8s Hacim:</b> +${volume_diff:,.0f}\n"
                                f"💰 <b>Anlık Fiyat:</b> {price} USDT\n"
                                f"📈 <b>24s Değişim:</b> %{change_24h:.2f}\n"
                                f"📊 <b>15m RSI:</b> {rsi_text}\n"
                                f"{btc_icon} <b>BTC Durumu:</b> %{btc_change:.2f}\n\n"
                                f"🔗 <a href='{chart_url}'>Binance Grafiğini Aç</a>"
                            )
                            
                            send_telegram_message(msg)
                
                previous_volumes[symbol] = quote_volume
                previous_prices[symbol] = price
                
        except Exception as e:
            print(f"Tarama Hatası (Önemsiz): {e}")
            
        time.sleep(CHECK_INTERVAL)

# ==========================================
# 6. SİSTEMİ ÇALIŞTIR
# ==========================================
if __name__ == "__main__":
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    start_scanner()
