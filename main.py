
import os
import time
import logging
from threading import Thread

import requests
import pandas as pd
from flask import Flask


# ============================================================
# BALİNA RADARI V3.1
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8740764565:AAEg2qstGT7nzILN00OKTNgammNPuZ-OZFM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "937967050")

MIN_VOLUME_TRY = 100000
MIN_VOLUME_USDT = 500000

SCAN_INTERVAL = 300
SIGNAL_COOLDOWN = 3600

EARLY_SCORE = 65
STRONG_SCORE = 80
WHALE_SCORE = 90

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
    "User-Agent": "BalinaRadari/3.1"
})


# ============================================================
# FLASK / RAILWAY
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Balina Radari V3.1 Aktif!"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radari V3.1"
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

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN bulunamadi.")
        return False

    if not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID bulunamadi.")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
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
                "Telegram API hatasi: %s",
                result
            )
            return False

        return True

    except Exception as error:
        logger.error(
            "Telegram baglanti hatasi: %s",
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

        if not isinstance(data, list):
            return []

        return data

    except Exception as error:
        logger.error(
            "Binance ticker hatasi: %s",
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

        if not isinstance(data, list):
            return []

        return data

    except Exception as error:
        logger.error(
            "%s %s veri hatasi: %s",
            symbol,
            interval,
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

    last_loss = average_loss.iloc[-1]

    if last_loss == 0:
        return 100.0

    rs = (
        average_gain.iloc[-1]
        / last_loss
    )

    return float(
        100 - (100 / (1 + rs))
    )


# ============================================================
# YUZDE DEGISIM
# ============================================================

def percent_change(old, new):

    if old <= 0:
        return 0.0

    return ((new - old) / old) * 100


# ============================================================
# COIN ANALIZI
# ============================================================

def analyze_coin(symbol):

    candles_5m = get_klines(
        symbol,
        "5m",
        100
    )

    if len(candles_5m) < 30:
        return None

    candles_15m = get_klines(
        symbol,
        "15m",
        100
    )

    if len(candles_15m) < 60:
        return None

    try:

        # Acik mumlari kullanmiyoruz.
        candles_5m = candles_5m[:-1]
        candles_15m = candles_15m[:-1]

        closes_5m = [
            float(candle[4])
            for candle in candles_5m
        ]

        volumes_5m = [
            float(candle[6])
            for candle in candles_5m
        ]

        taker_buys_5m = [
            float(candle[9])
            for candle in candles_5m
        ]

        closes_15m = [
            float(candle[4])
            for candle in candles_15m
        ]

        volumes_15m = [
            float(candle[6])
            for candle in candles_15m
        ]

        # ----------------------------------------------------
        # FIYAT
        # ----------------------------------------------------

        price = closes_5m[-1]

        # ----------------------------------------------------
        # MOMENTUM
        # ----------------------------------------------------

        momentum_5m = percent_change(
            closes_5m[-2],
            closes_5m[-1]
        )

        momentum_15m = percent_change(
            closes_5m[-4],
            closes_5m[-1]
        )

        momentum_30m = percent_change(
            closes_5m[-7],
            closes_5m[-1]
        )

        momentum_60m = percent_change(
            closes_5m[-13],
            closes_5m[-1]
        )

        # ----------------------------------------------------
        # 5M HACIM
        # ----------------------------------------------------

        current_volume = volumes_5m[-1]

        old_volumes = volumes_5m[-25:-1]

        if not old_volumes:
            return None

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

        volume_change = percent_change(
            volumes_5m[-2],
            current_volume
        )

        # ----------------------------------------------------
        # HACIM IVMESI
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
            volume_acceleration = 0

        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------

        if current_volume <= 0:
            return None

        buy_pressure = (
            taker_buys_5m[-1]
            / current_volume
        ) * 100

        pressures = []

        for index in range(-3, 0):

            volume = volumes_5m[index]

            if volume <= 0:
                continue

            pressure = (
                taker_buys_5m[index]
                / volume
            ) * 100

            pressures.append(pressure)

        if pressures:
            average_pressure = (
                sum(pressures)
                / len(pressures)
            )
        else:
            average_pressure = 0

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        rsi = calculate_rsi(
            closes_15m
        )

        if rsi is None:
            return None

        # ----------------------------------------------------
        # EMA
        # ----------------------------------------------------

        series = pd.Series(
            closes_15m,
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

        # ----------------------------------------------------
        # 15M HACIM
        # ----------------------------------------------------

        current_15m_volume = volumes_15m[-1]

        old_15m_volumes = volumes_15m[-21:-1]

        if not old_15m_volumes:
            return None

        average_15m_volume = (
            sum(old_15m_volumes)
            / len(old_15m_volumes)
        )

        if average_15m_volume > 0:
            volume_15m_ratio = (
                current_15m_volume
                / average_15m_volume
            )
        else:
            volume_15m_ratio = 0

        # ----------------------------------------------------
        # PUAN
        # ----------------------------------------------------

        score = 0
        reasons = []

        # Hacim
        if volume_ratio >= 4:
            score += 25
            reasons.append(
                "Hacim 4 katin uzerinde"
            )

        elif volume_ratio >= 3:
            score += 20
            reasons.append(
                "Hacim 3 katin uzerinde"
            )

        elif volume_ratio >= 2:
            score += 15
            reasons.append(
                "Hacim 2 katin uzerinde"
            )

        elif volume_ratio >= 1.5:
            score += 8
            reasons.append(
                "Hacim yukseliyor"
            )

        # Hacim degisimi
        if volume_change >= 100:
            score += 15
            reasons.append(
                "Son mumda hacim patlamasi"
            )

        elif volume_change >= 50:
            score += 10
            reasons.append(
                "Hacim hizlaniyor"
            )

        elif volume_change >= 25:
            score += 5
            reasons.append(
                "Hacim artiyor"
            )

        # Hacim ivmesi
        if volume_acceleration >= 100:
            score += 10
            reasons.append(
                "Hacim ivmesi cok guclu"
            )

        elif volume_acceleration >= 50:
            score += 6
            reasons.append(
                "Hacim ivmesi yukseliyor"
            )

        # Alici baskisi
        if buy_pressure >= 68:
            score += 20
            reasons.append(
                "Cok guclu alici baskisi"
            )

        elif buy_pressure >= 63:
            score += 15
            reasons.append(
                "Guclu alici baskisi"
            )

        elif buy_pressure >= 58:
            score += 10
            reasons.append(
                "Alici baskisi yukseliyor"
            )

        elif buy_pressure >= 54:
            score += 5
            reasons.append(
                "Alicilar hafif ustun"
            )

        # Son 3 mum
        if average_pressure >= 60:
            score += 10
            reasons.append(
                "Son 3 mumda guclu alici baskisi"
            )

        elif average_pressure >= 55:
            score += 5
            reasons.append(
                "Son 3 mumda alici baskisi pozitif"
            )

        # 5 dakika momentum
        if 0.3 <= momentum_5m <= 2:
            score += 10
            reasons.append(
                "5 dk hareket yeni basliyor"
            )

        elif 2 < momentum_5m <= 4:
            score += 6
            reasons.append(
                "5 dk momentum gucleniyor"
            )

        elif momentum_5m > 6:
            score -= 5
            reasons.append(
                "5 dk hareket hizlandi"
            )

        # 15 dakika momentum
        if 0 < momentum_15m < 4:
            score += 8
            reasons.append(
                "15 dk hareket erken asamada"
            )

        elif 4 <= momentum_15m < 8:
            score += 4
            reasons.append(
                "15 dk momentum basladi"
            )

        elif momentum_15m >= 10:
            score -= 10
            reasons.append(
                "15 dk hareket ilerlemis"
            )

        # 30 dakika momentum
        if 0 < momentum_30m < 7:
            score += 5
            reasons.append(
                "30 dk kontrollu yukselis"
            )

        elif momentum_30m >= 12:
            score -= 8
            reasons.append(
                "30 dk hareket fazla yukselmis"
            )

        # RSI
        if 45 <= rsi <= 62:
            score += 10
            reasons.append(
                "RSI erken hareket bolgesinde"
            )

        elif 62 < rsi <= 70:
            score += 5
            reasons.append(
                "RSI gucleniyor"
            )

        elif rsi > 78:
            score -= 10
            reasons.append(
                "RSI asiri yuksek"
            )

        # Trend
        if trend_up:
            score += 8
            reasons.append(
                "EMA trendi yukari"
            )

        # 15M hacim
        if volume_15m_ratio >= 2:
            score += 7
            reasons.append(
                "15 dk hacim guclu"
            )

        elif volume_15m_ratio >= 1.5:
            score += 4
            reasons.append(
                "15 dk hacim destekliyor"
            )

        score = max(
            0,
            min(score, 100)
        )

        # ----------------------------------------------------
        # GEC KALMIS HAREKETLERI ELE
        # ----------------------------------------------------

        if momentum_30m >= 15:
            return None

        if momentum_60m >= 25:
            return None

        # ----------------------------------------------------
        # SINYAL TURU
        # ----------------------------------------------------

        if score >= WHALE_SCORE:

            signal_type = (
                "COK GUCLU BALINA HAREKETI"
            )

        elif score >= STRONG_SCORE:

            signal_type = (
                "GUCLU ERKEN GIRIS ADAYI"
            )

        elif score >= EARLY_SCORE:

            signal_type = (
                "ERKEN HAREKET UYARISI"
            )

        else:
            return None

        return {
            "type": signal_type,
            "score": score,
            "price": price,
            "rsi": round(rsi, 1),
            "volume_ratio": round(
                volume_ratio,
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
            "average_pressure": round(
                average_pressure,
                1
            ),
            "momentum_5m": round(
                momentum_5m,
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
            "trend_up": trend_up,
            "reasons": reasons
        }

    except Exception as error:

        logger.error(
            "%s analiz hatasi: %s",
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

    elapsed = (
        time.time()
        - sent_signals[symbol]
    )

    return elapsed < SIGNAL_COOLDOWN


# ============================================================
# FIYAT FORMAT
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
        "- " + reason
        for reason in result["reasons"]
    )

    trend = (
        "YUKARI"
        if result["trend_up"]
        else "ZAYIF"
    )

    return (
        f"BALINA RADARI V3.1\n\n"
        f"{result['type']}\n\n"
        f"Coin: #{symbol}\n"
        f"Fiyat: {format_price(result['price'])}\n\n"
        f"Radar puani: {result['score']}/100\n"
        f"Hacim: {result['volume_ratio']}x\n"
        f"Hacim degisimi: %{result['volume_change']}\n"
        f"Hacim ivmesi: %{result['volume_acceleration']}\n"
        f"Alici baskisi: %{result['buy_pressure']}\n"
        f"Ortalama baski: %{result['average_pressure']}\n\n"
        f"5 dk: %{result['momentum_5m']}\n"
        f"15 dk: %{result['momentum_15m']}\n"
        f"30 dk: %{result['momentum_30m']}\n"
        f"60 dk: %{result['momentum_60m']}\n\n"
        f"RSI: {result['rsi']}\n"
        f"Trend: {trend}\n\n"
        f"NEDEN ALARM VERDI?\n"
        f"{reasons}\n\n"
        f"Analiz: Binance 5m + 15m\n"
        f"Not: Bu bir erken hareket alarmidir; "
        f"kazanc garantisi degildir."
    )


# ============================================================
# BASLANGIC TESTI
# ============================================================

def send_startup_message():

    message = (
        "BALINA RADARI V3.1 AKTIF\n\n"
        "Bot baslatildi.\n"
        "Telegram baglantisi calisiyor.\n"
        "Erken hareket taramasi hazir.\n\n"
        "TRY + USDT taramasi\n"
        "5 dk momentum\n"
        "Hacim patlamasi\n"
        "Hacim ivmesi\n"
        "Alici baskisi\n"
        "RSI\n"
        "EMA trendi\n"
        "Gec hareket filtresi\n\n"
        "Tarama araligi: 5 dakika"
    )

    return send_telegram(message)


# ============================================================
# SCAN LOOP
# ============================================================

def scan_loop():

    logger.info(
        "Balina Radari V3.1 baslatiliyor..."
    )

    startup_ok = send_startup_message()

    if startup_ok:

        logger.info(
            "Telegram baglantisi basarili."
        )

    else:

        logger.error(
            "Telegram baslangic mesaji gonderilemedi."
        )

    while True:

        start_time = time.time()

        try:

            logger.info(
                "Binance taramasi basladi."
            )

            tickers = get_tickers()

            if not tickers:

                logger.error(
                    "Binance ticker verisi alinamadi."
                )

                time.sleep(60)
                continue

            candidates = []

            for ticker in tickers:

                symbol = ticker.get(
                    "symbol",
                    ""
                )

                is_try = symbol.endswith("TRY")
                is_usdt = symbol.endswith("USDT")

                if not (is_try or is_usdt):
                    continue

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

                if is_try:
                    if quote_volume < MIN_VOLUME_TRY:
                        continue

                if is_usdt:
                    if quote_volume < MIN_VOLUME_USDT:
                        continue

                candidates.append(symbol)

            logger.info(
                "%d TRY/USDT cifti taranacak.",
                len(candidates)
            )

            signal_count = 0

            for symbol in candidates:

                if signal_on_cooldown(symbol):
                    continue

                result = analyze_coin(symbol)

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

                    sent_signals[symbol] = time.time()

                    signal_count += 1

                    logger.info(
                        "Sinyal gonderildi: %s | %d/100",
                        symbol,
                        result["score"]
                    )

                else:

                    logger.error(
                        "Telegram gonderilemedi: %s",
                        symbol
                    )

                time.sleep(0.25)

            logger.info(
                "Tarama tamamlandi. "
                "%d sinyal gonderildi.",
                signal_count
            )

        except Exception as error:

            logger.exception(
                "Tarama dongusu hatasi: %s",
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
            "%.0f saniye sonra yeni tarama.",
            wait_time
        )

        time.sleep(wait_time)


# ============================================================
# BASLAT
# ============================================================

if __name__ == "__main__":

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN Railway Variables "
            "icinde bulunamadi."
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID Railway Variables "
            "icinde bulunamadi."
        )

    logger.info(
        "Balina Radari V3.1 baslatiliyor."
    )

    Thread(
        target=run_flask,
        daemon=True
    ).start()

    scan_loop()
