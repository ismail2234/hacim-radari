import os
import time
import logging
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V4
# Erken hareket + hacim ivmesi + alıcı baskısı + momentum
# V3.1'in çalışan Railway/Gunicorn başlangıç yapısı korunmuştur.
# ============================================================


# ============================================================
# AYARLAR
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MIN_VOLUME_TRY = 100000
MIN_VOLUME_USDT = 500000

SCAN_INTERVAL = 300
SIGNAL_COOLDOWN = 3600

MAX_WORKERS = 10

# Kademeli sinyaller
EARLY_SCORE = 60
STRONG_SCORE = 75
WHALE_SCORE = 88

# Aynı anda çok fazla Telegram mesajı göndermemek için
MAX_SIGNALS_PER_SCAN = 5

sent_signals = {}
state_lock = Lock()


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("balina-radari-v4")


# ============================================================
# HTTP
# ============================================================

session = requests.Session()

adapter = requests.adapters.HTTPAdapter(
    pool_connections=30,
    pool_maxsize=30
)

session.mount("https://", adapter)

session.headers.update({
    "User-Agent": "BalinaRadari/4.0"
})


# ============================================================
# FLASK / RAILWAY
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V4 Aktif ve Çalışıyor!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V4"
    }


def run_flask():
    port = int(os.getenv("PORT", "8080"))

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error(
            "TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik!"
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = session.post(
            url,
            json=data,
            timeout=10
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            logger.error(
                "Telegram API hatası: %s",
                result
            )
            return False

        return True

    except Exception as error:
        logger.error(
            "Telegram bağlantı hatası: %s",
            error
        )
        return False


# ============================================================
# BINANCE
# ============================================================

def get_tickers():
    try:
        response = session.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        return data if isinstance(data, list) else []

    except Exception as error:
        logger.error(
            "Binance ticker hatası: %s",
            error
        )
        return []


def get_klines(symbol, interval, limit=100):
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:
        response = session.get(
            "https://api.binance.com/api/v3/klines",
            params=params,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        return data if isinstance(data, list) else []

    except Exception as error:
        logger.error(
            "%s %s veri hatası: %s",
            symbol,
            interval,
            error
        )
        return []


# ============================================================
# TEKNİK HESAPLAMALAR
# ============================================================

def percent_change(old, new):
    if old is None or new is None or old <= 0:
        return 0.0

    return ((new - old) / old) * 100


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None

    series = pd.Series(
        closes,
        dtype=float
    )

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    last_loss = average_loss.iloc[-1]

    if pd.isna(last_loss):
        return None

    if last_loss == 0:
        return 100.0

    rs = (
        average_gain.iloc[-1]
        / last_loss
    )

    return float(
        100 - (100 / (1 + rs))
    )


def format_price(price):
    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:,.4f}"

    if price >= 0.01:
        return f"{price:,.6f}"

    return f"{price:,.8f}"


# ============================================================
# COOLDOWN
# ============================================================

def signal_on_cooldown(symbol):
    with state_lock:
        last_sent = sent_signals.get(symbol)

    if last_sent is None:
        return False

    return (
        time.time() - last_sent
        < SIGNAL_COOLDOWN
    )


# ============================================================
# COIN ANALİZİ
# ============================================================

def analyze_coin(symbol):
    """
    V4 yaklaşımı:

    - 5m kapanmış mumlar ana referans
    - Açık 5m mum erken hareket için ayrıca kullanılır
    - 15m trend/RSI doğrulaması
    - Hacim oranı + hacim ivmesi
    - Taker buy baskısı
    - 5m/15m/30m momentum
    - Aşırı yükselmiş hareketleri geç sinyal olarak eleme
    """

    candles_5m = get_klines(
        symbol,
        "5m",
        100
    )

    candles_15m = get_klines(
        symbol,
        "15m",
        100
    )

    if len(candles_5m) < 35:
        return {
            "status": "insufficient",
            "reason": "5m veri yetersiz"
        }

    if len(candles_15m) < 60:
        return {
            "status": "insufficient",
            "reason": "15m veri yetersiz"
        }

    try:
        # ----------------------------------------------------
        # 5M KAPANMIŞ MUM VERİSİ
        # ----------------------------------------------------

        closed_5m = candles_5m[:-1]

        closes_5m = [
            float(candle[4])
            for candle in closed_5m
        ]

        volumes_5m = [
            float(candle[6])
            for candle in closed_5m
        ]

        taker_buys_5m = [
            float(candle[9])
            for candle in closed_5m
        ]

        # ----------------------------------------------------
        # AÇIK 5M MUM
        # ----------------------------------------------------

        live_5m = candles_5m[-1]

        live_price = float(live_5m[4])
        live_volume = float(live_5m[5])

        # Kline [7] = quote asset volume
        live_quote_volume = float(live_5m[7])

        live_taker_buy_quote = float(
            live_5m[10]
        )

        # Binance ticker/kline içindeki fiyatı kullanıyoruz.
        # Açık mum henüz tamamlanmadığı için onu geçmiş ortalamaya
        # doğrudan eşitlemiyoruz; yalnızca erken uyarı desteği olarak
        # kullanıyoruz.

        # ----------------------------------------------------
        # 15M KAPANMIŞ MUM VERİSİ
        # ----------------------------------------------------

        closed_15m = candles_15m[:-1]

        closes_15m = [
            float(candle[4])
            for candle in closed_15m
        ]

        volumes_15m = [
            float(candle[6])
            for candle in closed_15m
        ]

        # ----------------------------------------------------
        # TEMEL FİYAT
        # ----------------------------------------------------

        closed_price = closes_5m[-1]

        price = (
            live_price
            if live_price > 0
            else closed_price
        )

        # ----------------------------------------------------
        # MOMENTUM
        # ----------------------------------------------------

        momentum_5m = percent_change(
            closes_5m[-2],
            closed_price
        )

        momentum_15m = percent_change(
            closes_5m[-4],
            closed_price
        )

        momentum_30m = percent_change(
            closes_5m[-7],
            closed_price
        )

        momentum_60m = percent_change(
            closes_5m[-13],
            closed_price
        )

        live_momentum_5m = percent_change(
            closes_5m[-1],
            price
        )

        # ----------------------------------------------------
        # 5M HACİM
        # ----------------------------------------------------

        current_volume = volumes_5m[-1]

        old_volumes = volumes_5m[-25:-1]

        if len(old_volumes) < 10:
            return {
                "status": "insufficient",
                "reason": "hacim geçmişi yetersiz"
            }

        average_volume = (
            sum(old_volumes)
            / len(old_volumes)
        )

        if average_volume <= 0:
            return {
                "status": "insufficient",
                "reason": "ortalama hacim sıfır"
            }

        volume_ratio = (
            current_volume
            / average_volume
        )

        volume_change = percent_change(
            volumes_5m[-2],
            current_volume
        )

        # ----------------------------------------------------
        # HACİM İVMESİ
        # ----------------------------------------------------

        recent_volume = (
            sum(volumes_5m[-3:])
            / 3
        )

        previous_volume = (
            sum(volumes_5m[-6:-3])
            / 3
        )

        if previous_volume > 0:
            volume_acceleration = (
                (recent_volume - previous_volume)
                / previous_volume
            ) * 100
        else:
            volume_acceleration = 0.0

        # ----------------------------------------------------
        # AÇIK MUM HACİM İVME SİNYALİ
        # ----------------------------------------------------

        live_volume_ratio = (
            live_quote_volume / average_volume
            if average_volume > 0
            else 0
        )

        live_buy_pressure = (
            (
                live_taker_buy_quote
                / live_quote_volume
            ) * 100
            if live_quote_volume > 0
            else 0
        )

        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------

        if current_volume <= 0:
            return {
                "status": "insufficient",
                "reason": "güncel hacim sıfır"
            }

        buy_pressure = (
            taker_buys_5m[-1]
            / current_volume
        ) * 100

        pressures = []

        for index in range(-3, 0):
            volume = volumes_5m[index]

            if volume > 0:
                pressure = (
                    taker_buys_5m[index]
                    / volume
                ) * 100

                pressures.append(pressure)

        average_pressure = (
            sum(pressures) / len(pressures)
            if pressures
            else 0
        )

        # ----------------------------------------------------
        # RSI / EMA
        # ----------------------------------------------------

        rsi = calculate_rsi(
            closes_15m
        )

        if rsi is None:
            return {
                "status": "insufficient",
                "reason": "RSI hesaplanamadı"
            }

        series_15m = pd.Series(
            closes_15m,
            dtype=float
        )

        ema20 = series_15m.ewm(
            span=20,
            adjust=False
        ).mean().iloc[-1]

        ema50 = series_15m.ewm(
            span=50,
            adjust=False
        ).mean().iloc[-1]

        trend_up = ema20 > ema50

        # ----------------------------------------------------
        # 15M HACİM
        # ----------------------------------------------------

        current_15m_volume = volumes_15m[-1]

        old_15m_volumes = volumes_15m[-21:-1]

        if not old_15m_volumes:
            return {
                "status": "insufficient",
                "reason": "15m hacim geçmişi yetersiz"
            }

        average_15m_volume = (
            sum(old_15m_volumes)
            / len(old_15m_volumes)
        )

        volume_15m_ratio = (
            current_15m_volume
            / average_15m_volume
            if average_15m_volume > 0
            else 0
        )

        # ----------------------------------------------------
        # PUANLAMA
        # ----------------------------------------------------

        score = 0
        reasons = []

        # ====================================================
        # HACİM
        # ====================================================

        if volume_ratio >= 4:
            score += 22
            reasons.append(
                "🚀 Hacim 4x+"
            )

        elif volume_ratio >= 3:
            score += 18
            reasons.append(
                "🔥 Hacim 3x+"
            )

        elif volume_ratio >= 2:
            score += 14
            reasons.append(
                "📈 Hacim 2x+"
            )

        elif volume_ratio >= 1.5:
            score += 8
            reasons.append(
                "📊 Hacim normalin üzerinde"
            )

        # ====================================================
        # HACİM DEĞİŞİMİ
        # ====================================================

        if volume_change >= 100:
            score += 12
            reasons.append(
                "⚡ Son kapanan mumda hacim patlaması"
            )

        elif volume_change >= 50:
            score += 8
            reasons.append(
                "⚡ Hacim hızlanıyor"
            )

        elif volume_change >= 25:
            score += 4
            reasons.append(
                "📈 Hacim artıyor"
            )

        # ====================================================
        # HACİM İVMESİ
        # ====================================================

        if volume_acceleration >= 100:
            score += 10
            reasons.append(
                "🚀 Hacim ivmesi çok güçlü"
            )

        elif volume_acceleration >= 50:
            score += 7
            reasons.append(
                "🔥 Hacim ivmesi yükseliyor"
            )

        elif volume_acceleration >= 25:
            score += 3
            reasons.append(
                "📈 Hacim ivmesi pozitif"
            )

        # ====================================================
        # ALICI BASKISI
        # ====================================================

        if buy_pressure >= 68:
            score += 18
            reasons.append(
                "🐋 Çok güçlü alıcı baskısı"
            )

        elif buy_pressure >= 63:
            score += 14
            reasons.append(
                "🟢 Güçlü alıcı baskısı"
            )

        elif buy_pressure >= 58:
            score += 9
            reasons.append(
                "🟢 Alıcı baskısı pozitif"
            )

        elif buy_pressure >= 54:
            score += 4
            reasons.append(
                "🟡 Alıcı baskısı yükseliyor"
            )

        # ====================================================
        # SON 3 MUM BASKISI
        # ====================================================

        if average_pressure >= 62:
            score += 8
            reasons.append(
                "🐋 Son 3 mumda güçlü alıcı baskısı"
            )

        elif average_pressure >= 57:
            score += 5
            reasons.append(
                "🟢 Son 3 mum baskısı pozitif"
            )

        # ====================================================
        # AÇIK MUM ERKEN UYARI
        # ====================================================

        if live_volume_ratio >= 2:
            score += 8
            reasons.append(
                "⚡ Açık 5 dk mumunda olağandışı hacim"
            )

        elif live_volume_ratio >= 1.3:
            score += 4
            reasons.append(
                "📈 Açık 5 dk mumunda hacim hızlanıyor"
            )

        if live_buy_pressure >= 63:
            score += 7
            reasons.append(
                "🟢 Açık mumda güçlü alıcı baskısı"
            )

        elif live_buy_pressure >= 56:
            score += 3
            reasons.append(
                "🟡 Açık mumda alıcı baskısı artıyor"
            )

        if live_momentum_5m >= 0.5:
            score += 4
            reasons.append(
                "⚡ Fiyat canlı mumda yukarı hareket ediyor"
            )

        # ====================================================
        # MOMENTUM
        # ====================================================

        if 0.2 <= momentum_5m <= 2.5:
            score += 9
            reasons.append(
                "🎯 5 dk hareket erken aşamada"
            )

        elif 2.5 < momentum_5m <= 4.5:
            score += 5
            reasons.append(
                "📈 5 dk momentum güçleniyor"
            )

        elif momentum_5m > 6:
            score -= 6
            reasons.append(
                "⏰ 5 dk hareket fazla hızlandı"
            )

        if 0 < momentum_15m < 4:
            score += 8
            reasons.append(
                "🎯 15 dk hareket erken aşamada"
            )

        elif 4 <= momentum_15m < 7:
            score += 4
            reasons.append(
                "📈 15 dk momentum güçleniyor"
            )

        elif momentum_15m >= 10:
            score -= 8
            reasons.append(
                "⏰ 15 dk hareket ilerlemiş"
            )

        if 0 < momentum_30m < 7:
            score += 5
            reasons.append(
                "📈 30 dk kontrollü yükseliş"
            )

        elif momentum_30m >= 12:
            score -= 8
            reasons.append(
                "⏰ 30 dk hareket fazla ilerlemiş"
            )

        # ====================================================
        # RSI
        # ====================================================

        if 42 <= rsi <= 62:
            score += 10
            reasons.append(
                "📊 RSI erken hareket bölgesinde"
            )

        elif 62 < rsi <= 70:
            score += 5
            reasons.append(
                "📊 RSI güçleniyor"
            )

        elif rsi > 78:
            score -= 10
            reasons.append(
                "⚠️ RSI aşırı yüksek"
            )

        # ====================================================
        # EMA
        # ====================================================

        if trend_up:
            score += 7
            reasons.append(
                "📈 EMA trendi yukarı"
            )
        else:
            # Trend aşağı olsa bile hacim ve momentum
            # çok güçlüyse erken radar tamamen kapanmıyor.
            reasons.append(
                "〽️ EMA trendi henüz yukarı dönmemiş"
            )

        # ====================================================
        # 15M HACİM
        # ====================================================

        if volume_15m_ratio >= 2:
            score += 7
            reasons.append(
                "🔥 15 dk hacim güçlü"
            )

        elif volume_15m_ratio >= 1.5:
            score += 4
            reasons.append(
                "📊 15 dk hacim destekliyor"
            )

        # ====================================================
        # GEÇ HAREKET FİLTRESİ
        # ====================================================

        # V3.1'e göre biraz daha toleranslı.
        # Çok geç hareketleri yine de eliyoruz.
        if momentum_30m >= 18:
            return {
                "status": "late",
                "reason": "30 dk hareket fazla ilerlemiş"
            }

        if momentum_60m >= 30:
            return {
                "status": "late",
                "reason": "60 dk hareket fazla ilerlemiş"
            }

        # Negatif fiyat hareketi + zayıf baskı kombinasyonunu ele.
        if (
            momentum_15m < -3
            and buy_pressure < 55
            and volume_ratio < 1.5
        ):
            return {
                "status": "weak",
                "reason": "momentum ve alıcı baskısı zayıf"
            }

        score = max(
            0,
            min(int(score), 100)
        )

        # ====================================================
        # SİNYAL TÜRÜ
        # ====================================================

        if score >= WHALE_SCORE:
            signal_type = (
                "🚨 ÇOK GÜÇLÜ ERKEN HAREKET"
            )

        elif score >= STRONG_SCORE:
            signal_type = (
                "🟢 GÜÇLENEN ERKEN SİNYAL"
            )

        elif score >= EARLY_SCORE:
            signal_type = (
                "🟡 ERKEN HAREKET UYARISI"
            )

        else:
            return {
                "status": "below_score",
                "score": score,
                "reason": "minimum puana ulaşamadı"
            }

        return {
            "status": "signal",
            "type": signal_type,
            "score": score,
            "price": price,
            "rsi": round(rsi, 1),
            "volume_ratio": round(volume_ratio, 2),
            "live_volume_ratio": round(
                live_volume_ratio,
                2
            ),
            "volume_change": round(
                volume_change,
                1
            ),
            "volume_acceleration": round(
                volume_acceleration,
                1
            ),
            "buy_pressure": round(
                buy_pressure,
                1
            ),
            "live_buy_pressure": round(
                live_buy_pressure,
                1
            ),
            "average_pressure": round(
                average_pressure,
                1
            ),
            "momentum_5m": round(
                momentum_5m,
                2
            ),
            "live_momentum_5m": round(
                live_momentum_5m,
                2
            ),
            "momentum_15m": round(
                momentum_15m,
                2
            ),
            "momentum_30m": round(
                momentum_30m,
                2
            ),
            "momentum_60m": round(
                momentum_60m,
                2
            ),
            "volume_15m_ratio": round(
                volume_15m_ratio,
                2
            ),
            "trend_up": trend_up,
            "reasons": reasons
        }

    except Exception as error:
        logger.error(
            "%s analiz hatası: %s",
            symbol,
            error
        )

        return {
            "status": "error",
            "reason": str(error)
        }


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def create_message(symbol, result):
    reasons = "\n".join(
        f"• {reason}"
        for reason in result["reasons"]
    )

    trend = (
        "🟢 YUKARI"
        if result["trend_up"]
        else "🟡 HENÜZ DÖNMEDİ"
    )

    return (
        "🐋 BALİNA RADARI V4\n\n"
        f"{result['type']}\n\n"
        f"🪙 Coin: #{symbol}\n"
        f"💰 Fiyat: {format_price(result['price'])}\n\n"
        f"🎯 Radar Puanı: {result['score']}/100\n\n"
        f"🔥 Hacim: {result['volume_ratio']}x\n"
        f"⚡ Açık Mum Hacmi: "
        f"{result['live_volume_ratio']}x\n"
        f"📈 Hacim Değişimi: "
        f"%{result['volume_change']}\n"
        f"🚀 Hacim İvmesi: "
        f"%{result['volume_acceleration']}\n\n"
        f"🐋 Alıcı Baskısı: "
        f"%{result['buy_pressure']}\n"
        f"🟢 Açık Mum Baskısı: "
        f"%{result['live_buy_pressure']}\n"
        f"📊 Ortalama Baskı: "
        f"%{result['average_pressure']}\n\n"
        f"⚡ 5 dk Momentum: "
        f"%{result['momentum_5m']}\n"
        f"⚡ Canlı 5 dk: "
        f"%{result['live_momentum_5m']}\n"
        f"📈 15 dk Momentum: "
        f"%{result['momentum_15m']}\n"
        f"📈 30 dk Momentum: "
        f"%{result['momentum_30m']}\n"
        f"📈 60 dk Momentum: "
        f"%{result['momentum_60m']}\n\n"
        f"📊 RSI: {result['rsi']}\n"
        f"〽️ Trend: {trend}\n"
        f"🔥 15 dk Hacim: "
        f"{result['volume_15m_ratio']}x\n\n"
        f"🔎 NEDEN ALARM VERDİ?\n"
        f"{reasons}\n\n"
        "📌 Bu bir erken hareket uyarısıdır; "
        "yatırım garantisi değildir."
    )


# ============================================================
# ADAY ANALİZİ
# ============================================================

def process_candidate(symbol):
    if signal_on_cooldown(symbol):
        return {
            "symbol": symbol,
            "status": "cooldown"
        }

    result = analyze_coin(symbol)

    if not result:
        return {
            "symbol": symbol,
            "status": "error"
        }

    if result.get("status") != "signal":
        return {
            "symbol": symbol,
            "status": result.get(
                "status",
                "unknown"
            ),
            "score": result.get(
                "score",
                0
            )
        }

    return {
        "symbol": symbol,
        "status": "signal",
        "result": result
    }


# ============================================================
# SCANNER
# ============================================================

def scan_loop():
    logger.info(
        "🐋 Balina Radarı V4 başlatılıyor..."
    )

    send_telegram(
        "🐋 BALİNA RADARI V4 AKTİF\n\n"
        "✅ Scanner çalışıyor.\n"
        "🔎 TRY + USDT taraması aktif.\n"
        "🟡 Erken hareket\n"
        "🟢 Güçlenen sinyal\n"
        "🚨 Güçlü hareket\n"
        "⚡ Açık 5 dk mum takibi aktif.\n"
        "⏱️ Tarama aralığı: 5 dakika"
    )

    excluded = (
        "UPUSDT",
        "DOWNUSDT",
        "BULLUSDT",
        "BEARUSDT",
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "USDPUSDT",
        "DAIUSDT"
    )

    while True:
        start_time = time.time()

        try:
            logger.info(
                "🔎 Binance piyasa taraması başladı..."
            )

            tickers = get_tickers()

            if not tickers:
                logger.error(
                    "Binance verisi alınamadı. "
                    "60 saniye bekleniyor."
                )

                time.sleep(60)
                continue

            candidates = []

            try_count = 0
            usdt_count = 0

            for ticker in tickers:
                symbol = ticker.get(
                    "symbol",
                    ""
                )

                is_try = symbol.endswith("TRY")
                is_usdt = symbol.endswith("USDT")

                if not (is_try or is_usdt):
                    continue

                if any(
                    item in symbol
                    for item in excluded
                ):
                    continue

                try:
                    quote_volume = float(
                        ticker.get(
                            "quoteVolume",
                            0
                        )
                    )
                except (
                    ValueError,
                    TypeError
                ):
                    continue

                if (
                    is_try
                    and quote_volume < MIN_VOLUME_TRY
                ):
                    continue

                if (
                    is_usdt
                    and quote_volume < MIN_VOLUME_USDT
                ):
                    continue

                candidates.append(symbol)

                if is_try:
                    try_count += 1

                if is_usdt:
                    usdt_count += 1

            logger.info(
                "📋 Adaylar: %d | TRY: %d | USDT: %d",
                len(candidates),
                try_count,
                usdt_count
            )

            if not candidates:
                logger.warning(
                    "⚠️ Hacim filtresinden geçen coin yok."
                )

            stats = {
                "signal": 0,
                "below_score": 0,
                "late": 0,
                "weak": 0,
                "insufficient": 0,
                "cooldown": 0,
                "error": 0,
                "unknown": 0
            }

            signals = []

            logger.info(
                "⚡ %d aday paralel analiz ediliyor...",
                len(candidates)
            )

            with ThreadPoolExecutor(
                max_workers=MAX_WORKERS
            ) as executor:

                futures = [
                    executor.submit(
                        process_candidate,
                        symbol
                    )
                    for symbol in candidates
                ]

                for future in as_completed(
                    futures
                ):
                    try:
                        result = future.result()
                    except Exception as error:
                        logger.error(
                            "Worker hatası: %s",
                            error
                        )
                        stats["error"] += 1
                        continue

                    status = result.get(
                        "status",
                        "unknown"
                    )

                    if status in stats:
                        stats[status] += 1
                    else:
                        stats["unknown"] += 1

                    if status == "signal":
                        signals.append(result)

            # ------------------------------------------------
            # SİNYALLERİ PUANA GÖRE SIRALA
            # ------------------------------------------------

            signals.sort(
                key=lambda item: item["result"]["score"],
                reverse=True
            )

            sent_count = 0

            for item in signals:
                if sent_count >= MAX_SIGNALS_PER_SCAN:
                    break

                symbol = item["symbol"]
                result = item["result"]

                message = create_message(
                    symbol,
                    result
                )

                if send_telegram(message):
                    with state_lock:
                        sent_signals[symbol] = (
                            time.time()
                        )

                    sent_count += 1

                    logger.info(
                        "🐋 Sinyal gönderildi: "
                        "%s | %d/100",
                        symbol,
                        result["score"]
                    )

                time.sleep(0.4)

            # ------------------------------------------------
            # DETAYLI DIAGNOSTIC LOG
            # ------------------------------------------------

            logger.info(
                "📊 SONUÇ | Aday:%d | "
                "Sinyal:%d | "
                "60 altı:%d | "
                "Geç:%d | "
                "Zayıf:%d | "
                "Veri yetersiz:%d | "
                "Cooldown:%d | "
                "Hata:%d",
                len(candidates),
                sent_count,
                stats["below_score"],
                stats["late"],
                stats["weak"],
                stats["insufficient"],
                stats["cooldown"],
                stats["error"]
            )

            # En yüksek puanları logla.
            if signals:
                top = signals[:5]

                top_text = ", ".join(
                    f"{item['symbol']}="
                    f"{item['result']['score']}"
                    for item in top
                )

                logger.info(
                    "🏆 En yüksek adaylar: %s",
                    top_text
                )
            else:
                logger.info(
                    "🔕 Bu taramada sinyal oluşmadı."
                )

        except Exception as error:
            logger.exception(
                "Tarama döngüsü hatası: %s",
                error
            )

        elapsed = (
            time.time()
            - start_time
        )

        wait_time = max(
            1,
            SCAN_INTERVAL - elapsed
        )

        logger.info(
            "⏱️ Tarama %.1f saniye sürdü. "
            "%.0f saniye sonra tekrar başlayacak.",
            elapsed,
            wait_time
        )

        time.sleep(wait_time)


# ============================================================
# OTOMATİK BAŞLATICI
# ============================================================
# ⚠️ V3.1'DE ÇALIŞAN ALT KISIM KORUNDU.
# Railway / Gunicorn import ettiğinde scanner arka planda başlar.

scanner_thread = Thread(
    target=scan_loop,
    daemon=True,
    name="balina-scanner"
)

scanner_thread.start()


if __name__ == "__main__":
    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )
