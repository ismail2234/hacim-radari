
import os
import time
import requests
import threading
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Hacim Radarı Botu Aktif!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# Telegram Ayarları (Render Environment Variables üzerinden çekilir)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

COOLDOWN_SANIYE = 300
TARAMA_ARALIGI = 8

son_bildirim_zamanlari = {}
gecmis_toplam_hacim = {}

def telegram_mesaj_gonder(mesaj, sembol):
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN veya CHAT_ID eksik!")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mesaj,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=5)
        return r.status_code == 200
    except Exception as e:
        print("Telegram gonderme hatasi:", e)
        return False

def btc_durum_al():
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
        res = requests.get(url, timeout=5).json()
        return float(res.get("priceChangePercent", 0))
    except:
        return 0.0

def bot_thread():
    print("Bot taraması başlatıldı...")
    while True:
        try:
            btc_degisim = btc_durum_al()
            url = "https://api.binance.com/api/v3/ticker/24hr"
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                time.sleep(5)
                continue

            veri = response.json()
            simdiki_zaman = time.time()

            for item in veri:
                sembol = item.get("symbol", "")
                if not sembol.endswith("USDT"):
                    continue

                hacim = float(item.get("quoteVolume", 0))
                fiyat = float(item.get("lastPrice", 0))
                degisim = float(item.get("priceChangePercent", 0))

                if sembol in gecmis_toplam_hacim:
                    onceki_hacim = gecmis_toplam_hacim[sembol]
                    hacim_farki = hacim - onceki_hacim

                    if hacim_farki >= 30000:
                        son_zaman = son_bildirim_zamanlari.get(sembol, 0)
                        if simdiki_zaman - son_zaman > COOLDOWN_SANIYE:
                            fiyat_str = f"{fiyat:.6f}" if fiyat < 1 else f"{fiyat:.2f}"
                            btc_uyari = (
                                "\n⚠️ <i>Dikkat: BTC Düşüşte!</i>"
                                if btc_degisim < -1.5
                                else ""
                            )

                            if hacim_farki >= 100000:
                                yildiz = "⭐⭐⭐ (ÇOK GÜÇLÜ BALİNA)"
                            elif hacim_farki >= 60000:
                                yildiz = "⭐⭐ (ORTA-YÜKSEK)"
                            else:
                                yildiz = "⭐ (STANDART)"

                            mesaj = (
                                f"🔥 <b>HACİM SİNYALİ YAKALANDI!</b> 🔥\n"
                                f"Güç: {yildiz}\n\n"
                                f"🪙 <b>Coin:</b> #{sembol}\n"
                                f"💵 <b>Son 8s Hacim:</b> +${hacim_farki:,.0f}\n"
                                f"📈 <b>Fiyat:</b> {fiyat_str} USDT\n"
                                f"📊 <b>24s Değişim:</b> %{degisim:+.2f}\n"
                                f"🟢 <b>BTC Durumu:</b> %{btc_degisim:+.2f}"
                                f"{btc_uyari}"
                            )

                            if telegram_mesaj_gonder(mesaj, sembol):
                                son_bildirim_zamanlari[sembol] = simdiki_zaman

                gecmis_toplam_hacim[sembol] = hacim

            time.sleep(TARAMA_ARALIGI)
        except Exception as e:
            print("Bot hatasi:", e)
            time.sleep(5)

if __name__ == "__main__":
    t = threading.Thread(target=bot_thread)
    t.daemon = True
    t.start()
    run_flask()
          
