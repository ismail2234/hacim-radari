import os
import time
import logging
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from flask import Flask


# ============================================================
# 🔮 BALİNA RADARI V4.0 (KEHANET & AÇIK POZİSYON MOTORU)
# ============================================================


# ============================================================
# AYARLAR & SÜRELER
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 💰 MİNİMUM HACİM BARAJLARI (FUTURES)
MIN_VOLUME_USDT = 1000000     

# ⏱️ ZAMAN AYARLARI 
SCAN_INTERVAL = 300          
SIGNAL_COOLDOWN = 7200       # Kehanet sinyalleri için soğuma süresi 2 saat

# ⚡ EŞZAMANLI TARAMA
MAX_WORKERS = 10             

# 🎯 SİNYAL PUAN EŞİKLERİ
ORACLE_SCORE = 70
WHALE_SCORE = 85

sent_signals = {}
previous_open_interests = {}


# ============================================================
# LOG AYARLARI
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("kehanet-motoru")


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
session.mount("https://", adapter)
session.headers.update({"User-Agent": "KehanetMotoru/4.0"})


# ============================================================
# FLASK HEALTH CHECK
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🔮 Balina Radarı V4.0 (Kehanet Motoru) Aktif!"

@app.route("/health")
def health():
    return {"status": "ok", "bot": "Kehanet Motoru V4.0"}

def run_flask():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# ============================================================
# TELEGRAM BİLDİRİM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik!")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}

    try:
        response = session.post(url, json=data, timeout=8)
        response.raise_for_status()
        return response.json().get("ok", False)
    except Exception as error:
        logger.error("Telegram bağlantı hatası: %s", error)
        return False


# ============================================================
# BINANCE FUTURES API SERVİSLERİ
# ============================================================

def get_futures_tickers():
    try:
        response = session.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as error:
        logger.error("Futures ticker hatası: %s", error)
        return []

def get_open_interest(symbol):
    try:
        response = session.get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}", timeout=6)
        response.raise_for_status()
        data = response.json()
        return float(data.get("openInterest", 0))
    except Exception:
        return 0.0

def get_premium_index(symbol):
    try:
        response = session.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}", timeout=6)
        response.raise_for_status()
        data = response.json()
        return float(data.get("lastFundingRate", 0))
    except Exception:
        return 0.0

def get_klines(symbol, interval, limit=50):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        response = session.get("https://fapi.binance.com/fapi/v1/klines", params=params, timeout=8)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as error:
        return []


# ============================================================
# YARDIMCI HESAPLAMALAR
# ============================================================

def percent_change(old, new):
    if old <= 0: return 0.0
    return ((new - old) / old) * 100

def format_price(price):
    if price >= 1000: return f"{price:,.2f}"
    if price >= 1: return f"{price:,.4f}"
    if price >= 0.01: return f"{price:,.6f}"
    return f"{price:,.8f}"


# ============================================================
# KEHANET ANALİZ MOTORU (V4.0)
# ============================================================

def analyze_coin(symbol):
    candles = get_klines(symbol, "15m", 40)
    if len(candles) < 30:
        return None

    try:
        closes = [float(candle[4]) for candle in candles[:-1]]
        volumes = [float(candle[6]) for candle in candles[:-1]]
        
        price = closes[-1]
        
        # Momentum (Fiyat henüz patlamamış olmalı)
        momentum_1h = percent_change(closes[-5], closes[-1])
        momentum_4h = percent_change(closes[-17], closes[-1])

        # FOMO Koruma: Zaten fırlamışsa kehanet anlamını yitirir
        if momentum_1h >= 6 or momentum_4h >= 10:
            return None

        # 1. AÇIK POZİSYON (OI) TAKİBİ
        current_oi = get_open_interest(symbol)
        prev_oi = previous_open_interests.get(symbol, current_oi)
        previous_open_interests[symbol] = current_oi

        oi_change = percent_change(prev_oi, current_oi) if prev_oi > 0 else 0

        # 2. FONLAMA ORANI (FUNDING RATE)
        funding_rate = get_premium_index(symbol)

        # 3. HACİM SIKIŞMASI
        cur_vol = volumes[-1]
        avg_vol = sum(volumes[-20:-1]) / 19 if len(volumes) >= 20 else 1
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

        # PUANLAMA SİSTEMİ (KEHANET MANTIĞI)
        score = 0
        reasons = []

        # Fiyat yatayken OI (Açık Pozisyon) patlıyorsa -> Balina mal topluyor / Barut doluyor!
        if -1.5 <= momentum_1h <= 2.5 and oi_change >= 4.0:
            score += 35
            reasons.append(f"🔮 Gizli OI Patlaması (Fiyat Yatay, Açık Pozisyon +%{round(oi_change, 1)})")
        elif oi_change >= 7.0:
            score += 25
            reasons.append(f"Açık Pozisyon Hızla Büyüyor (+%{round(oi_change, 1)})")

        if vol_ratio >= 3.0 and -1.0 <= momentum_1h <= 3.0:
            score += 25
            reasons.append(f"Fiyat Sakin Ken Hacim Patlaması ({round(vol_ratio, 1)}x)")

        if funding_rate < -0.0005:
            score += 20
            reasons.append(f"Negatif Fonlama Baskısı (Short Squeeze Potansiyeli)")
        elif funding_rate > 0.0008:
            score += 10
            reasons.append(f"Yüksek Pozitif Fonlama")

        if momentum_4h <= 2.0:
            score += 15
            reasons.append("4 Saatlik Grafikte Tamamen Dip Sıkışması")

        score = max(0, min(score, 100))

        if score < ORACLE_SCORE:
            return None

        if score >= WHALE_SCORE:
            signal_type = "🚨 KEHANET: BÜYÜK PATLAMA YAKLAŞIYOR"
        else:
            signal_type = "🔮 ERKEN KEHANET SİNYALİ"

        return {
            "type": signal_type,
            "score": score,
            "price": price,
            "oi_change": round(oi_change, 2),
            "funding_rate": f"{funding_rate * 100:.4f}%",
            "momentum_1h": round(momentum_1h, 2),
            "reasons": reasons
        }

    except Exception as error:
        logger.error("%s kehanet analiz hatası: %s", symbol, error)
        return None


# ============================================================
# SİNYAL KONTROL & MESAJ
# ============================================================

def signal_on_cooldown(symbol):
    if symbol not in sent_signals:
        return False
    return (time.time() - sent_signals[symbol]) < SIGNAL_COOLDOWN

def create_message(symbol, result):
    reasons = "\n".join("- " + r for r in result["reasons"])

    return (
        f"🔮 BALİNA RADARI V4.0 (KEHANET MOTORU)\n\n"
        f"{result['type']}\n\n"
        f"🪙 Coin: #{symbol} (Futures)\n"
        f"💵 Fiyat: {format_price(result['price'])}\n\n"
        f"🎯 Kehanet Puanı: {result['score']}/100\n"
        f"📈 Açık Pozisyon Değişimi: %{result['oi_change']}\n"
        f"⚡ Fonlama Oranı: {result['funding_rate']}\n"
        f"⏱️ 1 Saatlik Değişim: %{result['momentum_1h']}\n\n"
        f"📌 TETİKLEYİCİ SEBEPLER:\n"
        f"{reasons}\n\n"
        f"⚠️ Not: Fiyat henüz patlamadan önceki hazırlık evresidir."
    )

def process_candidate(symbol):
    if signal_on_cooldown(symbol):
        return False

    result = analyze_coin(symbol)
    if result is None:
        return False

    message = create_message(symbol, result)
    if send_telegram(message):
        sent_signals[symbol] = time.time()
        logger.info("V4.0 Kehanet Sinyali Gönderildi: %s | Puan: %d", symbol, result["score"])
        return True
    return False


# ============================================================
# PARALEL TARAMA DÖNGÜSÜ
# ============================================================

def scan_loop():
    logger.info("Balina Radarı V4.0 (Kehanet Motoru) başlatılıyor...")

    send_telegram(
        "🔮 BALİNA RADARI V4.0 AKTİF\n\n"
        "✨ Kehanet Modu Devrede.\n"
        "📈 Açık Pozisyon (OI) Takibi Aktif.\n"
        "⚡ Fonlama Oranı Analizi Aktif.\n"
        "🎯 Hedef: Fiyat Kımıldamadan Önceki Hazırlık."
    )

    excluded = (
        "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT", "BTCUSDT", "ETHUSDT"
    )

    while True:
        start_time = time.time()
        try:
            logger.info("Binance Futures piyasa taraması başladı...")
            tickers = get_futures_tickers()

            if not tickers:
                logger.error("Futures verisi alınamadı, 60 sn bekleniyor.")
                time.sleep(60)
                continue

            candidates = []
            for ticker in tickers:
                symbol = ticker.get("symbol", "")
                if not symbol.endswith("USDT"): continue
                if symbol in excluded: continue

                try:
                    quote_volume = float(ticker.get("quoteVolume", 0))
                except (ValueError, TypeError):
                    continue

                if quote_volume < MIN_VOLUME_USDT: continue
                candidates.append(symbol)

            logger.info("%d adet Futures coin V4.0 kehanet motoruyla taranıyor...", len(candidates))

            signal_count = 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_candidate, symbol) for symbol in candidates]
                for future in as_completed(futures):
                    if future.result():
                        signal_count += 1

            logger.info("Kehanet taraması tamamlandı. %d sinyal gönderildi.", signal_count)

        except Exception as error:
            logger.exception("Kehanet tarama döngüsü hatası: %s", error)

        elapsed = time.time() - start_time
        wait_time = max(1, SCAN_INTERVAL - elapsed)
        logger.info("%.0f saniye sonra yeni kehanet taraması yapılacak.", wait_time)
        time.sleep(wait_time)


# ============================================================
# OTOMATİK BAŞLATICI
# ============================================================

scanner_thread = Thread(target=scan_loop, daemon=True, name="kehanet-scanner")
scanner_thread.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
  
