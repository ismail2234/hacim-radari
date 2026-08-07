
import time
import requests
from flask import Flask
from threading import Thread

# --- AYARLAR ---
TELEGRAM_BOT_TOKEN = "TOKENINI_BURAYA_YAZ"
TELEGRAM_CHAT_ID = "937967050"

MIN_VOLUME_USD = 15000
SCAN_INTERVAL = 300  # 5 dakika

# Tekrar mesaj engeli
sent_coins = {}

# Flask
app = Flask('')


@app.route('/')
def home():
    return "Bot aktif ve çalışıyor!"


def run_flask():
    app.run(host='0.0.0.0', port=10000)


# Telegram gönderim
def send_telegram_message(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=10
        )

        if response.status_code != 200:
            print("Telegram cevap:", response.text)

    except Exception as e:
        print("Telegram hatası:", e)



# Binance veri çekme
def get_binance_prices():

    url = "https://api.binance.com/api/v3/ticker/24hr"

    try:

        response = requests.get(
            url,
            timeout=10
        )

        return response.json()

    except Exception as e:

        print("Binance veri hatası:", e)

        return []



# Tarama sistemi
def start_scanner():

    print("🚀 Hacim radarı başladı")

    while True:

        try:

            data = get_binance_prices()

            current_time = time.time()


            for item in data:

                symbol = item.get("symbol")


                if not symbol.endswith("USDT"):
                    continue


                volume = float(item["quoteVolume"])
                price = float(item["lastPrice"])
                change = float(item["priceChangePercent"])


                if volume < MIN_VOLUME_USD:
                    continue


                # 2 saat içinde tekrar bildirme
                if symbol in sent_coins:

                    if current_time - sent_coins[symbol] < 7200:
                        continue


                sent_coins[symbol] = current_time


                message = (
                    f"🔥 *GÜÇLÜ HACİM SİNYALİ* 🔥\n\n"
                    f"🪙 Coin: #{symbol}\n"
                    f"💰 Fiyat: {price} USDT\n"
                    f"📊 24s Değişim: %{change:+.2f}\n"
                    f"🔥 24s Hacim: ${volume:,.0f}\n\n"
                    f"📱 Grafiği kontrol edin."
                )


                send_telegram_message(message)

                time.sleep(2)



            print("Tarama tamamlandı")

            time.sleep(SCAN_INTERVAL)



        except Exception as e:

            print("Tarama döngü hatası:", e)

            time.sleep(20)



# Başlat
if __name__ == "__main__":


    flask_thread = Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()


    send_telegram_message(
        "🚀 Bot başladı! Hacim radarı aktif."
    )


    start_scanner()
