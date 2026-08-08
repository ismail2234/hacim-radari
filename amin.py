
import os
import time
import logging
from threading import Thread

import requests
import pandas as pd
from flask import Flask


# ============================================================
# AYARLAR
# ============================================================

TELEGRAM_BOT_TOKEN = "8740764565:AAFwW-VRxTQQ_K0XFHtlwFteYGbefV0sjJM"
TELEGRAM_CHAT_ID = "937967050"

MIN_VOLUME_TRY = 100000
SCAN_INTERVAL = 300
SIGNAL_COOLDOWN = 10800

MIN_SCORE = 70

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
# HTTP
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BalinaRadari/2.0"
})


# ============================================================
# FLASK / RAILWAY
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V2 Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V2"
    }


def run_flask():
    port = int(os.environ.get("PORT", 8080))

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

        return response.json()

    except Exception as error:

        logger.error(
            "Binance ticker hatası: %s",
            error
        )

        return []


def get_klines(
    symbol,
    interval="15m",
    limit=60
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

        return response.json()

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

    if average_loss.iloc[-1] == 0:
        return 100.0

    rs = (
        average_gain.iloc[-1]
        / average_loss.iloc[-1]
    )

    return float(
        100 - (100 / (1 + rs))
    )


# ============================================================
# COIN ANALİZİ
# ============================================================

def analyze_coin(symbol):

    candles = get_klines(
        symbol,
        interval="15m",
        limit=60
    )

    if len(candles) < 50:
        return None

    try:

        # Henüz kapanmamış son mumu kullanmıyoruz.
        candles = candles[:-1]

        closes = [
            float(candle[4])
            for candle in candles
        ]

        # Binance kline:
        #
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
        # RSI
        # ====================================================

        rsi = calculate_rsi(closes)

        if rsi is None:
            return None

        # ====================================================
        # HACİM
        # ====================================================

        current_volume = volumes[-1]

        old_volumes = volumes[-21:-1]

        average_volume = (
            sum(old_volumes)
            / len(old_volumes)
        )

        if average_volume <= 0:
            return None

        volume_ratio = (
            current_volume
            / average_volume
        )

        volume_power = volume_ratio * 100

        # ====================================================
        # ALICI BASKISI
        # ====================================================

        if current_volume <= 0:
            return None

        buy_pressure = (
            taker_buys[-1]
            / current_volume
        ) * 100

        # ====================================================
        # FİYAT MOMENTUMU
        # ====================================================

        current_price = closes[-1]
        old_price = closes[-4]

        momentum = (
            (current_price - old_price)
            / old_price
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

        trend_up = ema20 > ema50

        # ====================================================
        # SON 3 MUM ALICI BASKISI
        # ====================================================

        last_three_pressures = []

        for i in range(-3, 0):

            volume = volumes[i]

            if volume <= 0:
                continue

            pressure = (
                taker_buys[i]
                / volume
            ) * 100

            last_three_pressures.append(
                pressure
            )

        strong_buying = (
            len(last_three_pressures) == 3
            and all(
                pressure >= 55
                for pressure in last_three_pressures
            )
        )

        # ====================================================
        # BALİNA PUANI
        # ====================================================

        score = 0
        reasons = []

        # ----------------------------------------------------
        # HACİM
        # ----------------------------------------------------

        if volume_ratio >= 3:

            score += 30

            reasons.append(
                "🚀 Hacim 3 katın üzerinde"
            )

        elif volume_ratio >= 2:

            score += 25

            reasons.append(
                "🔥 Hacim 2 katın üzerinde"
            )

        elif volume_ratio >= 1.5:

            score += 15

            reasons.append(
                "📈 Hacim artışı"
            )

        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------

        if buy_pressure >= 65:

            score += 25

            reasons.append(
                "🐋 Çok güçlü alıcı baskısı"
            )

        elif buy_pressure >= 60:

            score += 20

            reasons.append(
                "🟢 Güçlü alıcı baskısı"
            )

        elif buy_pressure >= 55:

            score += 10

            reasons.append(
                "🟢 Alıcılar güçlü"
            )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if 45 <= rsi <= 65:

            score += 15

            reasons.append(
                "📊 RSI uygun bölgede"
            )

        elif 65 < rsi <= 72:

            score += 5

            reasons.append(
                "📊 RSI yüksek"
            )

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        if trend_up:

            score += 10

            reasons.append(
                "📈 Genel trend yukarı"
            )

        # ----------------------------------------------------
        # MOMENTUM
        # ----------------------------------------------------

        if momentum >= 2:

            score += 10

            reasons.append(
                "🚀 Güçlü fiyat hareketi"
            )

        elif momentum > 0:

            score += 5

            reasons.append(
                "📈 Fiyat yukarı hareket ediyor"
            )

        # ----------------------------------------------------
        # 3 MUM ALICI BASKISI
        # ----------------------------------------------------

        if strong_buying:

            score += 10

            reasons.append(
                "🐋 3 mum boyunca güçlü alıcı baskısı"
            )

        score = min(score, 100)

        # ====================================================
        # SİNYAL FİLTRESİ
        # ====================================================

        if score < MIN_SCORE:
            return None

        if score >= 90:

            signal_type = (
                "🚨 ÇOK GÜÇLÜ BALİNA SİNYALİ"
            )

        elif score >= 80:

            signal_type = (
                "🔥 GÜÇLÜ BALİNA SİNYALİ"
            )

        else:

            signal_type = (
                "🐋 BALİNA AKÜMÜLASYON SİNYALİ"
            )

        return {
            "type": signal_type,
            "score": score,
            "price": current_price,
            "rsi": round(rsi, 1),
            "volume": round(volume_power, 0),
            "pressure": round(
                buy_pressure,
                1
            ),
            "momentum": round(
                momentum,
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
# TEKRAR SİNYAL KONTROLÜ
# ============================================================

def signal_on_cooldown(symbol):

    if symbol not in sent_signals:
        return False

    return (
        time.time()
        - sent_signals[symbol]
        < SIGNAL_COOLDOWN
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

def create_message(symbol, result):

    reasons = "\n".join(
        f"• {reason}"
        for reason in result["reasons"]
    )

    trend = (
        "🟢 YUKARI"
        if result["trend"]
        else "🔴 ZAYIF"
    )

    return (
        f"{result['type']}\n"
        f"\n"
        f"🪙 #{symbol}\n"
        f"💰 Fiyat: "
        f"{format_price(result['price'])} TRY\n"
        f"\n"
        f"🎯 BALİNA PUANI: "
        f"{result['score']}/100\n"
        f"🔥 Hacim: "
        f"%{result['volume']}\n"
        f"🐋 Alıcı Baskısı: "
        f"%{result['pressure']}\n"
        f"📊 RSI: "
        f"{result['rsi']}\n"
        f"📈 Momentum: "
        f"%{result['momentum']}\n"
        f"〽️ Trend: {trend}\n"
        f"\n"
        f"🔎 NEDEN SİNYAL GELDİ?\n"
        f"{reasons}\n"
        f"\n"
        f"⏱️ Analiz: 15 dakika"
    )


# ============================================================
# SCANNER
# ============================================================

def scanner():

    logger.info(
        "🐋 Balina Radarı V2 başladı."
    )

    while True:

        start_time = time.time()

        try:

            tickers = get_tickers()

            candidates = []

            # ------------------------------------------------
            # 24 SAATLİK HACİM FİLTRESİ
            # ------------------------------------------------

            for coin in tickers:

                symbol = coin.get(
                    "symbol",
                    ""
                )

                if not symbol.endswith("TRY"):
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

                candidates.append(symbol)

            logger.info(
                "%d TRY çifti taranacak.",
                len(candidates)
            )

            # ------------------------------------------------
            # ANALİZ
            # ------------------------------------------------

            for symbol in candidates:

                if signal_on_cooldown(symbol):
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

                    sent_signals[symbol] = (
                        time.time()
                    )

                    logger.info(
                        "🐋 Sinyal gönderildi: "
                        "%s | %d/100",
                        symbol,
                        result["score"]
                    )

                time.sleep(0.2)

        except Exception as error:

            logger.exception(
                "Tarama hatası: %s",
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
            "Tarama bitti. "
            "%.0f saniye sonra tekrar başlayacak.",
            wait_time
        )

        time.sleep(
            wait_time
        )


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "Telegram bot token bulunamadı."
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Telegram Chat ID bulunamadı."
        )

    Thread(
        target=run_flask,
        daemon=True
    ).start()

    scanner()
