
import os
import time
import requests
from flask import Flask
from threading import Thread

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


@app.route("/")
def home():
    return "🐋 Balina Radar PRO Aktif"


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        print(e)


def scanner():
    while True:
        print("Radar taraması çalışıyor...")
        
        # Buraya analiz motorlarını ekleyeceğiz:
        # RSI
        # MACD
        # EMA
        # Hacim
        # Balina analizi

        time.sleep(300)


def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(
        host="0.0.0.0",
        port=port
    )


if __name__ == "__main__":

    Thread(
        target=run_server,
        daemon=True
    ).start()

    scanner()
