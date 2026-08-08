
import os
import time
import logging
from threading import Thread

import requests
import pandas as pd
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V2.2
# ERKEN HAREKET TESPİT SİSTEMİ
# ============================================================


# ============================================================
# AYARLAR
# ============================================================

TELEGRAM_BOT_TOKEN = "8740764565:AAFwW-VRxTQQ_K0XFHtlwFteYGbefV0sjJM"
TELEGRAM_CHAT_ID = "937967050"

# 24 saatlik minimum TRY hacmi
MIN_VOLUME_TRY = 100000

# Kaç saniyede bir taransın?
SCAN_INTERVAL = 300

# Aynı coin için tekrar sinyal süresi
SIGNAL_COOLDOWN = 10800

# Erken uyarı minimum puanı
EARLY_SCORE = 55

# Güçlenen sinyal minimum puanı
STRONG_SCORE = 70

# Çok güçlü sinyal
WHALE_SCORE = 85

sent_signals = {}


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("balina-radari")


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BalinaRadari/2.2"
})


# ============================================================
# FLASK / RAILWAY
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V2.2 Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V2.2"
    }


def run_flask():

    port = int(
        os.environ.get(
            "PORT",
            8080
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

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

        logger.info(
            "Telegram mesajı gönderildi."
        )

        return True

    except Exception as error:

        logger.error(
            "Telegram bağlantı hatası: %s",
            error
        )

        return False


# ============================================================
# BINANCE TICKER
# ============================================================

def get_tickers():

    try:

        response = session.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):

            logger.error(
                "Binance beklenmeyen cevap verdi."
            )

            return []

        return data

    except Exception as error:

        logger.error(
            "Binance ticker hatası: %s",
            error
        )

        return []


# ============================================================
# BINANCE MUM VERİSİ
# ============================================================

def get_klines(
    symbol,
    interval="15m",
    limit=100
):

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

        if not isinstance(data, list):

            return []

        return data

    except Exception as error:

        logger.error(
            "%s mum verisi alınamadı: %s",
            symbol,
            error
        )

        return []


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    closes,
    period=14
):

    if len(closes) < period + 1:

        return None

    series = pd.Series(
        closes,
        dtype=float
    )

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

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

    if average_loss.iloc[-1] == 0:

        return 100.0

    rs = (
        average_gain.iloc[-1]
        /
        average_loss.iloc[-1]
    )

    return float(
        100 -
        (
            100 /
            (1 + rs)
        )
    )


# ============================================================
# COIN ANALİZİ
# ============================================================

def analyze_coin(symbol):

    candles = get_klines(
        symbol,
        interval="15m",
        limit=100
    )

    if len(candles) < 60:

        return None

    try:

        # Son mum henüz kapanmamış olabilir.
        candles = candles[:-1]

        closes = [
            float(candle[4])
            for candle in candles
        ]

        highs = [
            float(candle[2])
            for candle in candles
        ]

        lows = [
            float(candle[3])
            for candle in candles
        ]

        # [6] = Quote asset volume
        # [9] = Taker buy quote volume

        volumes = [
            float(candle[6])
            for candle in candles
        ]

        taker_buys = [
            float(candle[9])
            for candle in candles
        ]

        # ====================================================
        # FİYAT
        # ====================================================

        price = closes[-1]

        # ====================================================
        # RSI
        # ====================================================

        rsi = calculate_rsi(
            closes
        )

        if rsi is None:

            return None

        # ====================================================
        # HACİM PATLAMASI
        # ====================================================

        current_volume = volumes[-1]

        average_volume_20 = (
            sum(volumes[-21:-1])
            /
            len(volumes[-21:-1])
        )

        if average_volume_20 <= 0:

            return None

        volume_ratio = (
            current_volume
            /
            average_volume_20
        )

        volume_percent = (
            volume_ratio * 100
        )

        # ====================================================
        # HACİM DEĞİŞİM HIZI
        # ====================================================

        previous_volume = volumes[-2]

        if previous_volume > 0:

            volume_change = (
                (
                    current_volume
                    -
                    previous_volume
                )
                /
                previous_volume
            ) * 100

        else:

            volume_change = 0

        # ====================================================
        # ALICI BASKISI
        # ====================================================

        if current_volume <= 0:

            return None

        buy_pressure = (
            taker_buys[-1]
            /
            current_volume
        ) * 100

        # ====================================================
        # ÖNCEKİ MUM ALICI BASKISI
        # ====================================================

        previous_pressure = 0

        if previous_volume > 0:

            previous_pressure = (
                taker_buys[-2]
                /
                previous_volume
            ) * 100

        pressure_change = (
            buy_pressure
            -
            previous_pressure
        )

        # ====================================================
        # FİYAT MOMENTUMU
        # ====================================================

        price_15m = closes[-1]
        price_30m = closes[-3]
        price_60m = closes[-5]

        momentum_30m = (
            (
                price_15m
                -
                price_30m
            )
            /
            price_30m
        ) * 100

        momentum_60m = (
            (
                price_15m
                -
                price_60m
            )
            /
            price_60m
        ) * 100

        # ====================================================
        # EMA TREND
        # ====================================================

        series = pd.Series(
            closes,
            dtype=float
        )

        ema20 = series.ewm(
            span=20,
            adjust=False
        ).mean().iloc[-1]

        ema50 = series.ewm(
            span=50,
            adjust=False
        ).mean().iloc[-1]

        trend_up = (
            ema20 > ema50
        )

        # ====================================================
        # SON 3 MUM ALICI BASKISI
        # ====================================================

        pressures = []

        for i in range(-3, 0):

            volume = volumes[i]

            if volume <= 0:

                continue

            pressure = (
                taker_buys[i]
                /
                volume
            ) * 100

            pressures.append(
                pressure
            )

        three_candle_buying = (
            len(pressures) == 3
            and
            sum(pressures) / 3 >= 55
        )

        # ====================================================
        # SON 3 MUM HACİM
        # ====================================================

        recent_volume = (
            sum(volumes[-3:])
            /
            3
        )

        volume_acceleration = (
            recent_volume
            /
            average_volume_20
        )

        # ====================================================
        # PUAN
        # ====================================================

        score = 0

        reasons = []

        # ----------------------------------------------------
        # HACİM
        # ----------------------------------------------------

        if volume_ratio >= 3:

            score += 25

            reasons.append(
                "🚀 Hacim normalin 3 katından fazla"
            )

        elif volume_ratio >= 2:

            score += 20

            reasons.append(
                "🔥 Hacim normalin 2 katından fazla"
            )

        elif volume_ratio >= 1.5:

            score += 15

            reasons.append(
                "📈 Hacim normalden yüksek"
            )

        elif volume_ratio >= 1.25:

            score += 8

            reasons.append(
                "🟡 Hacim yükselmeye başladı"
            )

        # ----------------------------------------------------
        # HACİM HIZLANMASI
        # ----------------------------------------------------

        if volume_change >= 100:

            score += 15

            reasons.append(
                "⚡ Hacim çok hızlı artıyor"
            )

        elif volume_change >= 50:

            score += 10

            reasons.append(
                "⚡ Hacim hızlanıyor"
            )

        elif volume_change >= 25:

            score += 5

            reasons.append(
                "📈 Hacim artış hızı yükseliyor"
            )

        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------

        if buy_pressure >= 65:

            score += 20

            reasons.append(
                "🐋 Çok güçlü alıcı baskısı"
            )

        elif buy_pressure >= 60:

            score += 15

            reasons.append(
                "🟢 Güçlü alıcı baskısı"
            )

        elif buy_pressure >= 55:

            score += 10

            reasons.append(
                "🟢 Alıcı baskısı güçlü"
            )

        # ----------------------------------------------------
        # BASKI DEĞİŞİMİ
        # ----------------------------------------------------

        if pressure_change >= 8:

            score += 10

            reasons.append(
                "🐋 Alıcı baskısı belirgin şekilde artıyor"
            )

        elif pressure_change >= 4:

            score += 5

            reasons.append(
                "📈 Alıcı baskısı yükseliyor"
            )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if 45 <= rsi <= 65:

            score += 10

            reasons.append(
                "📊 RSI erken hareket bölgesinde"
            )

        elif 65 < rsi <= 72:

            score += 4

            reasons.append(
                "📊 RSI yükseliyor"
            )

        elif rsi > 78:

            score -= 10

            reasons.append(
                "⚠️ RSI aşırı yüksek"
            )

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        if trend_up:

            score += 8

            reasons.append(
                "📈 EMA trendi yukarı"
            )

        # ----------------------------------------------------
        # MOMENTUM
        # ----------------------------------------------------

        if 0 < momentum_30m < 3:

            score += 8

            reasons.append(
                "🟢 Fiyat henüz aşırı yükselmemiş"
            )

        elif 3 <= momentum_30m < 7:

            score += 4

            reasons.append(
                "📈 Fiyat hareketi başladı"
            )

        elif momentum_30m >= 10:

            score -= 10

            reasons.append(
                "⚠️ Hareket zaten çok ilerlemiş"
            )

        # ----------------------------------------------------
        # 3 MUM ALICI BASKISI
        # ----------------------------------------------------

        if three_candle_buying:

            score += 8

            reasons.append(
                "🐋 Son 3 mumda alıcı baskısı güçlü"
            )

        # ----------------------------------------------------
        # HACİM AKÜMÜLASYONU
        # ----------------------------------------------------

        if volume_acceleration >= 1.5:

            score += 5

            reasons.append(
                "🔥 Son mumlarda hacim birikiyor"
            )

        score = max(
            0,
            min(score, 100)
        )

        # ====================================================
        # SİNYAL TİPİ
        # ====================================================

        if score >= WHALE_SCORE:

            signal_type = (
                "🚨 ÇOK GÜÇLÜ BALİNA SİNYALİ"
            )

        elif score >= STRONG_SCORE:

            signal_type = (
                "🟢 GÜÇLENEN SİNYAL"
            )

        elif score >= EARLY_SCORE:

            signal_type = (
                "🟡 ERKEN UYARI"
            )

        else:

            return None

        # ====================================================
        # AŞIRI YÜKSELENLERİ FİLTRELE
        # ====================================================

        if momentum_30m >= 15:

            return None

        return {

            "type": signal_type,

            "score": score,

            "price": price,

            "rsi": round(
                rsi,
                1
            ),

            "volume": round(
                volume_percent,
                0
            ),

            "volume_change": round(
                volume_change,
                1
            ),

            "pressure": round(
                buy_pressure,
                1
            ),

            "pressure_change": round(
                pressure_change,
                1
            ),

            "momentum_30m": round(
                momentum_30m,
                2
            ),

            "momentum_60m": round(
                momentum_60m,
                2
            ),

            "trend": trend_up,

            "reasons": reasons
        }

    except Exception as error:

        logger.error(
            "%s analiz hatası: %s",
            symbol,
            error
        )

        return None


# ============================================================
# COOLDOWN
# ============================================================

def signal_on_cooldown(symbol):

    if symbol not in sent_signals:

        return False

    return (
        time.time()
        -
        sent_signals[symbol]
        <
        SIGNAL_COOLDOWN
    )


# ============================================================
# FİYAT
# ============================================================

def format_price(price):

    if price >= 1000:

        return f"{price:,.2f}"

    if price >= 1:

        return f"{price:,.4f}"

    if price >= 0.01:

        return f"{price:,.6f}"

    return f"{price:,.8f}"


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def create_message(
    symbol,
    result
):

    reasons = "\n".join(
        f"• {reason}"
        for reason in result["reasons"]
    )

    if result["trend"]:

        trend = "🟢 YUKARI"

    else:

        trend = "🔴 ZAYIF"

    return (

        f"{result['type']}\n"
        f"\n"

        f"🪙 #{symbol}\n"

        f"💰 Fiyat: "
        f"{format_price(result['price'])} TRY\n"

        f"\n"

        f"🎯 PUAN: "
        f"{result['score']}/100\n"

        f"🔥 Hacim: "
        f"%{result['volume']}\n"

        f"⚡ Hacim değişimi: "
        f"%{result['volume_change']}\n"

        f"🐋 Alıcı baskısı: "
        f"%{result['pressure']}\n"

        f"📈 Baskı değişimi: "
        f"%{result['pressure_change']}\n"

        f"📊 RSI: "
        f"{result['rsi']}\n"

        f"🚀 30 dk momentum: "
        f"%{result['momentum_30m']}\n"

        f"📈 60 dk momentum: "
        f"%{result['momentum_60m']}\n"

        f"〽️ Trend: "
        f"{trend}\n"

        f"\n"

        f"🔎 NEDEN UYARI GELDİ?\n"

        f"{reasons}\n"

        f"\n"

        f"⏱️ Analiz: 15 dakika\n"

        f"⚠️ Bu bir piyasa sinyalidir, "
        f"garantili kazanç değildir."
    )


# ============================================================
# BAŞLANGIÇ TESTİ
# ============================================================

def send_startup_message():

    message = (

        "🐋 BALİNA RADARI V2.2 AKTİF\n\n"

        "✅ Bot başlatıldı.\n"

        "✅ Telegram bağlantısı çalışıyor.\n"

        "🔎 Erken hareket taraması hazır.\n"

        "🟡 Erken Uyarı\n"
        "🟢 Güçlenen Sinyal\n"
        "🚨 Balina Sinyali\n\n"

        "⏱️ Tarama aralığı: 5 dakika"
    )

    return send_telegram(
        message
    )


# ============================================================
# SCANNER
# ============================================================

def scanner():

    logger.info(
        "🐋 Balina Radarı V2.2 başladı."
    )

    send_startup_message()

    while True:

        start_time = time.time()

        try:

            logger.info(
                "🔎 Binance taraması başladı."
            )

            tickers = get_tickers()

            if not tickers:

                logger.error(
                    "❌ Binance verisi alınamadı."
                )

            candidates = []

            # =================================================
            # TRY PARİTELERİ
            # =================================================

            for coin in tickers:

                symbol = coin.get(
                    "symbol",
                    ""
                )

                if not symbol.endswith(
                    "TRY"
                ):

                    continue

                try:

                    quote_volume = float(
                        coin.get(
                            "quoteVolume",
                            0
                        )
                    )

                except (
                    ValueError,
                    TypeError
                ):

                    continue

                if quote_volume < MIN_VOLUME_TRY:

                    continue

                candidates.append(
                    symbol
                )

            logger.info(
                "📊 %d TRY çifti taranacak.",
                len(candidates)
            )

            # =================================================
            # ANALİZ
            # =================================================

            signal_count = 0

            for symbol in candidates:

                if signal_on_cooldown(
                    symbol
                ):

                    continue

                result = analyze_coin(
                    symbol
                )

                if result is None:

                    continue

                message = create_message(
                    symbol,
                    result
                )

                sent = send_telegram(
                    message
                )

                if sent:

                    sent_signals[
                        symbol
                    ] = time.time()

                    signal_count += 1

                    logger.info(
                        "🚨 %s | %s | %d/100",
                        symbol,
                        result["type"],
                        result["score"]
                    )

                time.sleep(
                    0.2
                )

            logger.info(
                "✅ Tarama tamamlandı. "
                "%d sinyal bulundu.",
                signal_count
            )

        except Exception as error:

            logger.exception(
                "❌ Tarama hatası: %s",
                error
            )

        elapsed = (
            time.time()
            -
            start_time
        )

        wait_time = max(
            1,
            SCAN_INTERVAL
            -
            elapsed
        )

        logger.info(
            "⏱️ %.0f saniye sonra "
            "yeniden taranacak.",
            wait_time
        )

        time.sleep(
            wait_time
        )


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":

    if (
        not TELEGRAM_BOT_TOKEN
        or
        TELEGRAM_BOT_TOKEN.startswith(
            "BURAYA_"
        )
    ):

        raise RuntimeError(
            "Telegram bot token ayarlanmamış."
        )

    if (
        not TELEGRAM_CHAT_ID
        or
        TELEGRAM_CHAT_ID.startswith(
            "BURAYA_"
        )
    ):

        raise RuntimeError(
            "Telegram Chat ID ayarlanmamış."
        )

    logger.info(
        "🚀 Balina Radarı V2.2 başlatılıyor..."
    )

    Thread(
        target=run_flask,
        daemon=True
    ).start()

    scanner()
