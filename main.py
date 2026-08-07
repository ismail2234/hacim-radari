
import time
import requests
import pandas as pd
from flask import Flask
from threading import Thread


# ================= AYARLAR =================

TELEGRAM_BOT_TOKEN = "BURAYA_TOKEN"
TELEGRAM_CHAT_ID = "937967050"

MIN_VOLUME_USD = 100000
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
