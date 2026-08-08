import os
import time
import logging
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V3.1 (KATILI OPTİMİZE EDİLMİŞ)
# ============================================================


# ============================================================
# AYARLAR & SÜRELER
# ============================================================

# Telegram Bilgileri (Railway Variables Üzerinden Okunur)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 💰 MİNİMUM HACİM BARAJLARI
MIN_VOLUME_TRY = 100000      # 24s Min TRY Hacmi (100 Bin TL)
MIN_VOLUME_USDT = 500000     # 24s Min USDT Hacmi (500 Bin $)

# ⏱️ ZAMAN AYARLARI (SANİYE CİNSİNDEN)
SCAN_INTERVAL = 300          # Tarama Aralığı (300 Saniye = 5 Dakika)
SIGNAL_COOLDOWN = 3600       # Aynı Coin Tekrar Alarm Bekleme Süresi (3600 Saniye = 1 Saat)

# ⚡ EŞZAMANLI TARAMA KAPASİTESİ (HIZ İÇİN)
MAX_WORKERS = 10             # Aynı anda taranacak coin sayısı

# 🎯 SİNYAL PUAN EŞİKLERİ
EARLY_SCORE = 65
STRONG_SCORE = 80
WHALE_SCORE = 90

sent_signals = {}


# ============================================================
# LOG AYARLARI
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("balina-radari")


# ============================================================
# HTTP SESSION (BAĞLANTI OPTİMİZASYONU)
# ============================================================

session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
session.mount("https://", adapter)
session.headers.update({
    "User-Agent": "BalinaRadari/3.1"
})


# ============================================================
# FLASK / RAILWAY HEALTH CHECK
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🐋 Balina Radarı V3.1 Aktif ve Çalışıyor!"

@app.route("/health")
def health():
    return {"status": "ok", "bot": "Balina Radarı V3.1"}

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
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = session.post(url, json=data, timeout=8)
        response.raise_for_status()
        result = response.json()
        return result.get("ok", False)
    except Exception as error:
        logger.error("Telegram bağlantı hatası: %s", error)
        return False


# ============================================================
# BINANCE API SERVİSLERİ
# ============================================================

def get_tickers():
    try:
        response = session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as error:
        logger.error("Binance ticker hatası: %s", error)
        return []

def get_klines(symbol, interval, limit=100):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        response = session.get("https://api.binance.com/api/v3/klines", params=params, timeout=8)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as error:
        logger.error("%s %s veri hatası: %s", symbol, interval, error)
        return []


# ============================================================
# TEKNİK HESAPLAMALAR
# ============================================================

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None

    series = pd.Series(closes, dtype=float)
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    last_loss = average_loss.iloc[-1]
    if last_loss == 0:
        return 100.0

    rs = average_gain.iloc[-1] / last_loss
    return float(100 - (100 / (1 + rs)))

def percent_change(old, new):
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100

def format_price(price):
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}"
    if price >= 0.01:
        return f"{price:,.6f}"
    return f"{price:,.8f}"


# ============================================================
# COIN ANALİZ MOTORU
# ============================================================

def analyze_coin(symbol):
    candles_5m = get_klines(symbol, "5m", 100)
    if len(candles_5m) < 30:
        return None

    candles_15m = get_klines(symbol, "15m", 100)
    if len(candles_15m) < 60:
        return None

    try:
        # Açık olan son mumu analiz dışı bırakıyoruz
        candles_5m = candles_5m[:-1]
        candles_15m = candles_15m[:-1]

        closes_5m = [float(candle[4]) for candle in candles_5m]
        volumes_5m = [float(candle[6]) for candle in candles_5m]
        taker_buys_5m = [float(candle[9]) for candle in candles_5m]

        closes_15m = [float(candle[4]) for candle in candles_15m]
        volumes_15m = [float(candle[6]) for candle in candles_15m]

        price = closes_5m[-1]

        # Momentum Hesaplamaları
        momentum_5m = percent_change(closes_5m[-2], closes_5m[-1])
        momentum_15m = percent_change(closes_5m[-4], closes_5m[-1])
        momentum_30m = percent_change(closes_5m[-7], closes_5m[-1])
        momentum_60m = percent_change(closes_5m[-13], closes_5m[-1])

        # 5M Hacim Hesaplamaları
        current_volume = volumes_5m[-1]
        old_volumes = volumes_5m[-25:-1]
        if not old_volumes:
            return None

        average_volume = sum(old_volumes) / len(old_volumes)
        if average_volume <= 0:
            return None

        volume_ratio = current_volume / average_volume
        volume_change = percent_change(volumes_5m[-2], current_volume)

        # Hacim İvmesi
        recent_volume = sum(volumes_5m[-3:]) / 3
        previous_volume = sum(volumes_5m[-6:-3]) / 3
        volume_acceleration = ((recent_volume - previous_volume) / previous_volume) * 100 if previous_volume > 0 else 0

        # Alıcı Baskısı
        if current_volume <= 0:
            return None

        buy_pressure = (taker_buys_5m[-1] / current_volume) * 100

        pressures = []
        for index in range(-3, 0):
            vol = volumes_5m[index]
            if vol > 0:
                pressures.append((taker_buys_5m[index] / vol) * 100)
        average_pressure = sum(pressures) / len(pressures) if pressures else 0

        # RSI & EMA
        rsi = calculate_rsi(closes_15m)
        if rsi is None:
            return None

        series = pd.Series(closes_15m, dtype=float)
        ema20 = series.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = series.ewm(span=50, adjust=False).mean().iloc[-1]
        trend_up = ema20 > ema50

        # 15M Hacim
        current_15m_volume = volumes_15m[-1]
        old_15m_volumes = volumes_15m[-21:-1]
        if not old_15m_volumes:
            return None

        average_15m_volume = sum(old_15m_volumes) / len(old_15m_volumes)
        volume_15m_ratio = (current_15m_volume / average_15m_volume) if average_15m_volume > 0 else 0

        # PUANLAMA SİSTEMİ
        score = 0
        reasons = []

        if volume_ratio >= 4:
            score += 25
            reasons.append("Hacim 4 katın üzerinde")
        elif volume_ratio >= 3:
            score += 20
            reasons.append("Hacim 3 katın üzerinde")
        elif volume_ratio >= 2:
            score += 15
            reasons.append("Hacim 2 katın üzerinde")
        elif volume_ratio >= 1.5:
            score += 8
            reasons.append("Hacim yükseliyor")

        if volume_change >= 100:
            score += 15
            reasons.append("Son mumda hacim patlaması")
        elif volume_change >= 50:
            score += 10
            reasons.append("Hacim hızlanıyor")
        elif volume_change >= 25:
            score += 5
            reasons.append("Hacim artıyor")

        if volume_acceleration >= 100:
            score += 10
            reasons.append("Hacim ivmesi çok güçlü")
        elif volume_acceleration >= 50:
            score += 6
            reasons.append("Hacim ivmesi yükseliyor")

        if buy_pressure >= 68:
            score += 20
            reasons.append("Çok güçlü alıcı baskısı")
        elif buy_pressure >= 63:
            score += 15
            reasons.append("Güçlü alıcı baskısı")
        elif buy_pressure >= 58:
            score += 10
            reasons.append("Alıcı baskısı yükseliyor")

        if average_pressure >= 60:
            score += 10
            reasons.append("Son 3 mumda güçlü alıcı baskısı")
        elif average_pressure >= 55:
            score += 5
            reasons.append("Son 3 mumda alıcı baskısı pozitif")

        if 0.3 <= momentum_5m <= 2:
            score += 10
            reasons.append("5 dk hareket yeni başlıyor")
        elif 2 < momentum_5m <= 4:
            score += 6
            reasons.append("5 dk momentum güçleniyor")
        elif momentum_5m > 6:
            score -= 5
            reasons.append("5 dk hareket hızlandı")

        if 0 < momentum_15m < 4:
            score += 8
            reasons.append("15 dk hareket erken aşamada")
        elif 4 <= momentum_15m < 8:
            score += 4
            reasons.append("15 dk momentum başladı")
        elif momentum_15m >= 10:
            score -= 10
            reasons.append("15 dk hareket ilerlemiş")

        if 0 < momentum_30m < 7:
            score += 5
            reasons.append("30 dk kontrollü yükseliş")
        elif momentum_30m >= 12:
            score -= 8
            reasons.append("30 dk hareket fazla yükselmiş")

        if 45 <= rsi <= 62:
            score += 10
            reasons.append("RSI erken hareket bölgesinde")
        elif 62 < rsi <= 70:
            score += 5
            reasons.append("RSI güçleniyor")
        elif rsi > 78:
            score -= 10
            reasons.append("RSI aşırı yüksek")

        if trend_up:
            score += 8
            reasons.append("EMA trendi yukarı")

        if volume_15m_ratio >= 2:
            score += 7
            reasons.append("15 dk hacim güçlü")
        elif volume_15m_ratio >= 1.5:
            score += 4
            reasons.append("15 dk hacim destekliyor")

        score = max(0, min(score, 100))

        # Geç kalmış hareketleri ele
        if momentum_30m >= 15 or momentum_60m >= 25:
            return None

        # Sinyal Türü
        if score >= WHALE_SCORE:
            signal_type = "🚨 ÇOK GÜÇLÜ BALİNA HAREKETİ"
        elif score >= STRONG_SCORE:
            signal_type = "🟢 GÜÇLÜ ERKEN GİRİŞ ADAYI"
        elif score >= EARLY_SCORE:
            signal_type = "🟡 ERKEN HAREKET UYARISI"
        else:
            return None

        return {
            "type": signal_type,
            "score": score,
            "price": price,
            "rsi": round(rsi, 1),
            "volume_ratio": round(volume_ratio, 2),
            "volume_change": round(volume_change, 1),
            "volume_acceleration": round(volume_acceleration, 1),
            "buy_pressure": round(buy_pressure, 1),
            "average_pressure": round(average_pressure, 1),
            "momentum_5m": round(momentum_5m, 2),
            "momentum_15m": round(momentum_15m, 2),
            "momentum_30m": round(momentum_30m, 2),
            "momentum_60m": round(momentum_60m, 2),
            "trend_up": trend_up,
            "reasons": reasons
        }

    except Exception as error:
        logger.error("%s analiz hatası: %s", symbol, error)
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
    trend = "YUKARI" if result["trend_up"] else "ZAYIF"

    return (
        f"🐋 BALİNA RADARI V3.1\n\n"
        f"{result['type']}\n\n"
        f"Coin: #{symbol}\n"
        f"Fiyat: {format_price(result['price'])}\n\n"
        f"Radar Puanı: {result['score']}/100\n"
        f"Hacim Oranı: {result['volume_ratio']}x\n"
        f"Hacim Değişimi: %{result['volume_change']}\n"
        f"Hacim İvmesi: %{result['volume_acceleration']}\n"
        f"Alıcı Baskısı: %{result['buy_pressure']}\n"
        f"Ortalama Baskı: %{result['average_pressure']}\n\n"
        f"5 dk Momentum: %{result['momentum_5m']}\n"
        f"15 dk Momentum: %{result['momentum_15m']}\n"
        f"30 dk Momentum: %{result['momentum_30m']}\n"
        f"60 dk Momentum: %{result['momentum_60m']}\n\n"
        f"RSI: {result['rsi']}\n"
        f"Trend: {trend}\n\n"
        f"🔍 Neden Alarm Verdi?\n"
        f"{reasons}\n\n"
        f"📌 Not: Bu bir erken hareket uyarısıdır."
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
        logger.info("Sinyal gönderildi: %s | Puan: %d", symbol, result["score"])
        return True
    return False


# ============================================================
# PARALEL TARAMA DÖNGÜSÜ
# ============================================================

def scan_loop():
    logger.info("Balina Radarı V3.1 başlatılıyor...")

    send_telegram(
        "🐋 BALİNA RADARI V3.1 AKTİF\n\n"
        "✅ Hızlı Paralel Scanner Çalışıyor.\n"
        "🔎 TRY + USDT Taraması Aktif.\n"
        "⏱️ Tarama Aralığı: 5 Dakika"
    )

    excluded = (
        "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT",
        "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "USDPUSDT", "DAIUSDT"
    )

    while True:
        start_time = time.time()
        try:
            logger.info("Binance piyasa taraması başladı...")
            tickers = get_tickers()

            if not tickers:
                logger.error("Binance verisi alınamadı, 60 sn bekleniyor.")
                time.sleep(60)
                continue

            candidates = []
            for ticker in tickers:
                symbol = ticker.get("symbol", "")
                is_try = symbol.endswith("TRY")
                is_usdt = symbol.endswith("USDT")

                if not (is_try or is_usdt):
                    continue

                if any(item in symbol for item in excluded):
                    continue

                try:
                    quote_volume = float(ticker.get("quoteVolume", 0))
                except (ValueError, TypeError):
                    continue

                if is_try and quote_volume < MIN_VOLUME_TRY:
                    continue
                if is_usdt and quote_volume < MIN_VOLUME_USDT:
                    continue

                candidates.append(symbol)

            logger.info("%d adet aday coin paralel taranıyor...", len(candidates))

            signal_count = 0
            # EŞZAMANLI HIZLI PARALEL TARAMA (10 WORKER)
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_candidate, symbol) for symbol in candidates]
                for future in as_completed(futures):
                    if future.result():
                        signal_count += 1

            logger.info("Tarama tamamlandı. %d sinyal gönderildi.", signal_count)

        except Exception as error:
            logger.exception("Tarama döngüsü hatası: %s", error)

        elapsed = time.time() - start_time
        wait_time = max(1, SCAN_INTERVAL - elapsed)
        logger.info("%.0f saniye sonra yeni tarama yapılacak.", wait_time)
        time.sleep(wait_time)


# ============================================================
# OTOMATİK BAŞLATICI (GUNICORN / RAILWAY UYUMLU)
# ============================================================

# Railway / Gunicorn projeyi import ettiği an tarama motorunu arka planda başlatır
scanner_thread = Thread(target=scan_loop, daemon=True, name="balina-scanner")
scanner_thread.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


