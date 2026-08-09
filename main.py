import os
import time
import logging
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V3.2 (ERKEN UYARI SİSTEMİ)
# ============================================================


# ============================================================
# AYARLAR & SÜRELER
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 💰 MİNİMUM HACİM BARAJLARI
MIN_VOLUME_TRY = 100000      
MIN_VOLUME_USDT = 500000     

# ⏱️ ZAMAN AYARLARI 
SCAN_INTERVAL = 300          
SIGNAL_COOLDOWN = 3600       

# ⚡ EŞZAMANLI TARAMA (HIZ İÇİN)
MAX_WORKERS = 10             

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
session.headers.update({"User-Agent": "BalinaRadari/3.2"})


# ============================================================
# FLASK / RAILWAY HEALTH CHECK
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "🐋 Balina Radarı V3.2 (Erken Uyarı Sistemi) Aktif!"

@app.route("/health")
def health():
    return {"status": "ok", "bot": "Balina Radarı V3.2"}

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
    if old <= 0: return 0.0
    return ((new - old) / old) * 100

def format_price(price):
    if price >= 1000: return f"{price:,.2f}"
    if price >= 1: return f"{price:,.4f}"
    if price >= 0.01: return f"{price:,.6f}"
    return f"{price:,.8f}"


# ============================================================
# COIN ANALİZ MOTORU (V3.2 ERKEN UYARI)
# ============================================================

def analyze_coin(symbol):
    # 1 Dakikalık Mikroskobik Veri (YENİ)
    candles_1m = get_klines(symbol, "1m", 30)
    if len(candles_1m) < 20:
        return None
        
    candles_5m = get_klines(symbol, "5m", 100)
    if len(candles_5m) < 30:
        return None

    candles_15m = get_klines(symbol, "15m", 100)
    if len(candles_15m) < 60:
        return None

    try:
        # Açık olan son mumu analiz dışı bırakıyoruz
        candles_1m = candles_1m[:-1]
        candles_5m = candles_5m[:-1]
        candles_15m = candles_15m[:-1]

        # 1M Verileri
        volumes_1m = [float(candle[6]) for candle in candles_1m]
        taker_buys_1m = [float(candle[9]) for candle in candles_1m]
        current_1m_vol = volumes_1m[-1]
        taker_buy_1m_pct = (taker_buys_1m[-1] / current_1m_vol * 100) if current_1m_vol > 0 else 0

        # 5M ve 15M Verileri
        closes_5m = [float(candle[4]) for candle in candles_5m]
        volumes_5m = [float(candle[6]) for candle in candles_5m]
        taker_buys_5m = [float(candle[9]) for candle in candles_5m]

        closes_15m = [float(candle[4]) for candle in candles_15m]
        volumes_15m = [float(candle[6]) for candle in candles_15m]
        highs_15m = [float(candle[2]) for candle in candles_15m[-21:-1]]
        lows_15m = [float(candle[3]) for candle in candles_15m[-21:-1]]

        price = closes_5m[-1]

        # Momentum
        momentum_5m = percent_change(closes_5m[-2], closes_5m[-1])
        momentum_15m = percent_change(closes_5m[-4], closes_5m[-1])
        momentum_30m = percent_change(closes_5m[-7], closes_5m[-1])

        # FOMO Koruması: Zaten patlamış olanı ele!
        if momentum_15m >= 8 or momentum_30m >= 12:
            return None

        # 1. YENİ DEDEKTÖR: SIKIŞMA (SQUEEZE)
        max_h = max(highs_15m)
        min_l = min(lows_15m)
        squeeze_pct = ((max_h - min_l) / min_l) * 100 if min_l > 0 else 100
        is_squeezed = squeeze_pct < 3.5  # Son saatlerde fiyat max %3.5 oynamış (Sıkışmış)

        # 5M Hacim Hesaplamaları
        current_volume = volumes_5m[-1]
        old_volumes = volumes_5m[-25:-1]
        average_volume = sum(old_volumes) / len(old_volumes) if old_volumes else 0
        if average_volume <= 0: return None
        volume_ratio = current_volume / average_volume

        # 15M Hacim
        current_15m_volume = volumes_15m[-1]
        old_15m_volumes = volumes_15m[-21:-1]
        average_15m_volume = sum(old_15m_volumes) / len(old_15m_volumes) if old_15m_volumes else 0
        volume_15m_ratio = (current_15m_volume / average_15m_volume) if average_15m_volume > 0 else 0

        # 2. YENİ DEDEKTÖR: GİZLİ TOPLAMA (ACCUMULATION)
        is_accumulating = (0 < momentum_15m < 2.5) and (volume_15m_ratio >= 3.0)

        # RSI & EMA
        rsi = calculate_rsi(closes_15m)
        if rsi is None: return None
        
        series = pd.Series(closes_15m, dtype=float)
        ema20 = series.ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = series.ewm(span=50, adjust=False).mean().iloc[-1]
        trend_up = ema20 > ema50

        # PUANLAMA SİSTEMİ
        score = 0
        reasons = []

        # --- YENİ ÖNCÜ SİNYAL PUANLARI ---
        if is_squeezed:
            score += 15
            reasons.append("🌪️ Fiyat Sıkışması (Fırtına Öncesi Sessizlik)")
            
        if is_accumulating:
            score += 25
            reasons.append("🕵️‍♂️ Gizli Mal Toplanıyor (Yatay Fiyat + Yüksek Hacim)")
            
        if taker_buy_1m_pct >= 75:
            score += 20
            reasons.append("⚔️ 1 Dakikalık Agresif Balina Alımı (Taker > %75)")
        elif taker_buy_1m_pct >= 60:
            score += 10
            reasons.append("1 Dakikalık Güçlü Alış Baskısı")

        # --- KLASİK DESTEK SİNYALLERİ ---
        if volume_ratio >= 4:
            score += 20
            reasons.append("5 Dk Hacim 4 Katın Üzerinde")
        elif volume_ratio >= 2:
            score += 10
            reasons.append("5 Dk Hacim 2 Katın Üzerinde")

        buy_pressure = (taker_buys_5m[-1] / current_volume) * 100 if current_volume > 0 else 0
        if buy_pressure >= 65:
            score += 15
            reasons.append("Güçlü Alıcı Baskısı (5m)")

        if 0.5 <= momentum_5m <= 3:
            score += 10
            reasons.append("Hareket Yeni Başlıyor")

        if trend_up:
            score += 5
            reasons.append("Trend Yukarı Döndü")

        if 45 <= rsi <= 65:
            score += 10
            reasons.append("RSI Patlama Bölgesinde")

        score = max(0, min(score, 100))

        # Sinyal Türü Belirleme
        if score >= WHALE_SCORE:
            signal_type = "🚨 ÇOK GÜÇLÜ ERKEN UYARI"
        elif score >= STRONG_SCORE:
            signal_type = "🟢 GÜÇLÜ ERKEN GİRİŞ ADAYI"
        elif score >= EARLY_SCORE:
            signal_type = "🟡 POTANSİYEL HAREKET BAŞLANGICI"
        else:
            return None

        return {
            "type": signal_type,
            "score": score,
            "price": price,
            "rsi": round(rsi, 1),
            "volume_ratio": round(volume_ratio, 2),
            "taker_1m_pct": round(taker_buy_1m_pct, 1),
            "momentum_15m": round(momentum_15m, 2),
            "is_squeezed": is_squeezed,
            "is_accumulating": is_accumulating,
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
    
    # Rozetler
    s_badge = "✅" if result["is_squeezed"] else "❌"
    a_badge = "✅" if result["is_accumulating"] else "❌"

    return (
        f"🐋 BALİNA RADARI V3.2 (ERKEN UYARI)\n\n"
        f"{result['type']}\n\n"
        f"🪙 Coin: #{symbol}\n"
        f"💵 Fiyat: {format_price(result['price'])}\n\n"
        f"🎯 Radar Puanı: {result['score']}/100\n"
        f"📊 Hacim Oranı: {result['volume_ratio']}x\n"
        f"📈 15 Dk Değişim: %{result['momentum_15m']}\n"
        f"⚔️ 1 Dk Agresif Alım: %{result['taker_1m_pct']}\n"
        f"📉 RSI: {result['rsi']}\n\n"
        f"🔍 ÖNCÜ GÖSTERGELER\n"
        f"Fiyat Sıkışması: {s_badge}\n"
        f"Gizli Toplama: {a_badge}\n\n"
        f"📌 NEDEN ALARM VERDİ?\n"
        f"{reasons}\n\n"
        f"⚠️ Not: Kripto piyasası yüksek risk içerir."
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
        logger.info("V3.2 Sinyal Gönderildi: %s | Puan: %d", symbol, result["score"])
        return True
    return False


# ============================================================
# PARALEL TARAMA DÖNGÜSÜ
# ============================================================

def scan_loop():
    logger.info("Balina Radarı V3.2 (Erken Uyarı Sistemi) başlatılıyor...")

    send_telegram(
        "🐋 BALİNA RADARI V3.2 AKTİF\n\n"
        "✅ Yeni Öncü Dedektörler Devrede.\n"
        "🌪️ Sıkışma Filtresi Aktif.\n"
        "🕵️‍♂️ Gizli Toplama Algoritması Aktif.\n"
        "⚔️ 1 Dakikalık Agresif Alım Radarı Aktif.\n"
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

                if not (is_try or is_usdt): continue
                if any(item in symbol for item in excluded): continue

                try:
                    quote_volume = float(ticker.get("quoteVolume", 0))
                except (ValueError, TypeError):
                    continue

                if is_try and quote_volume < MIN_VOLUME_TRY: continue
                if is_usdt and quote_volume < MIN_VOLUME_USDT: continue

                candidates.append(symbol)

            logger.info("%d adet coin V3.2 motoruyla paralel taranıyor...", len(candidates))

            signal_count = 0
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

scanner_thread = Thread(target=scan_loop, daemon=True, name="balina-scanner")
scanner_thread.start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
      
