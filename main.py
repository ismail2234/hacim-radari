
import time
import requests
import pandas as pd
from flask import Flask
from threading import Thread


# ================= AYARLAR =================

TELEGRAM_BOT_TOKEN = "7547167571:AAF8u7jXvK7fPZ3yF4yXw7k3" 
TELEGRAM_CHAT_ID = "937967050"

MIN_VOLUME_USD = 15000
SCAN_INTERVAL = 300


# Daha önce gönderilen sinyaller
sent_signals = {}


# ================= FLASK =================

app = Flask(__name__)


@app.route("/")
def home():
    return "Binance Radar Aktif!"


def run_flask():
    app.run(host="0.0.0.0", port=10000)



# ================= TELEGRAM =================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        requests.post(
            url,
            json=data,
            timeout=10
        )

    except Exception as e:
        print("Telegram hata:", e)



# ================= BINANCE =================

def get_tickers():

    url = (
        "https://api.binance.com/"
        "api/v3/ticker/24hr"
    )

    try:

        r = requests.get(
            url,
            timeout=10
        )

        return r.json()

    except Exception as e:

        print("Ticker hata:", e)
        return []



def get_klines(symbol):

    url = (
        "https://api.binance.com/"
        "api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": "15m",
        "limit": 100
    }

    try:

        r = requests.get(
            url,
            params=params,
            timeout=10
        )

        return r.json()

    except Exception:

        return []



# ================= RSI =================

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


# ================= MACD =================

def calculate_macd(closes):

    def ema(data, period):
        k = 2 / (period + 1)
        ema_value = data[0]

        for price in data[1:]:
            ema_value = (price * k) + (ema_value * (1 - k))

        return ema_value

    ema12 = ema(closes[-50:], 12)
    ema26 = ema(closes[-50:], 26)

    macd_value = ema12 - ema26

    if macd_value > 0:
        return "YUKARI"

    return "ZAYIF"



# ================= ANALİZ =================

def analyze_coin(symbol):

    candles = get_klines(symbol)

    if not candles or len(candles) < 50:
        return None


    closes = []
    volumes = []


    for c in candles:

        closes.append(float(c[4]))
        volumes.append(float(c[5]))



    rsi = calculate_rsi(closes)

    macd = calculate_macd(closes)


    # Son hacim / ortalama hacim

    last_volume = volumes[-1]

    avg_volume = sum(volumes[-20:-1]) / 19

    volume_power = (last_volume / avg_volume) * 100



    if (
        rsi >= 40
        and rsi <= 65
        and macd == "YUKARI"
        and volume_power >= 150
    ):

        return {
            "rsi": round(rsi,2),
            "macd": macd,
            "volume": round(volume_power,2),
            "price": closes[-1]
        }


    return None




# ================= TARAMA =================

def scanner():

    print("🚀 Binance RSI MACD Radar başladı")


    while True:

        try:

            tickers = get_tickers()

            now = time.time()


            for coin in tickers:

                symbol = coin["symbol"]


                if not symbol.endswith("USDT"):
                    continue


                volume = float(
                    coin["quoteVolume"]
                )


                if volume < MIN_VOLUME_USD:
                    continue



                # tekrar sinyal engeli

                if symbol in sent_signals:

                    if now - sent_signals[symbol] < 7200:
                        continue



                result = analyze_coin(symbol)


                if result:


                    sent_signals[symbol] = now


                    message = (
                        f"🚨 *POTANSİYEL HAREKET*\n\n"
                        f"🪙 Coin: #{symbol}\n"
                        f"💰 Fiyat: {result['price']} USDT\n"
                        f"📊 RSI: {result['rsi']}\n"
                        f"🟢 MACD: {result['macd']}\n"
                        f"🔥 Hacim Gücü: %{result['volume']}\n\n"
                        f"Grafik kontrol edilmeli."
                    )


                    send_telegram(message)


                    time.sleep(2)



            print("Tarama tamamlandı")

            time.sleep(SCAN_INTERVAL)



        except Exception as e:

            print("Tarama hatası:", e)

            time.sleep(20)
if __name__ == "__main__":
    # Flask sunucusunu arka planda (daemon thread) çalıştır
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    send_telegram(
        "🚀 Binance Global RSI+MACD Radar aktif!"
    )

    # Tarayıcıyı ana akışta başlat
    scanner()
    
