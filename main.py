import os
import time
import logging
from threading import Thread

import requests
import pandas as pd
from flask import Flask


# ============================================================
# 🐋 BALİNA RADARI V3
# ERKEN HAREKET / HACİM / ALIM BASKISI SİSTEMİ
# ============================================================


# ============================================================
# AYARLAR
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8740764565:AAEg2qstGT7nzILN00OKTNgammNPuZ-OZFM"
)

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    "937967050"
)

# 24 saatlik minimum TRY hacmi
MIN_VOLUME_TRY = 100000

# Ana tarama aralığı
SCAN_INTERVAL = 300

# Aynı coin için tekrar alarm süresi
SIGNAL_COOLDOWN = 3600

# Sinyal seviyeleri
WATCH_SCORE = 50
EARLY_SCORE = 65
STRONG_SCORE = 78
WHALE_SCORE = 90

sent_signals = {}


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(
    "balina-radari-v3"
)


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "BalinaRadari/3.0"
})


# ============================================================
# FLASK / RAILWAY
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():

    return "🐋 Balina Radarı V3 Aktif!"


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V3"
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

    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "Telegram token bulunamadı."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        logger.error(
            "Telegram Chat ID bulunamadı."
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
# BINANCE TICKERS
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
            "Binance ticker hatası: %s",
            error
        )

        return []


# ============================================================
# BINANCE KLINES
# ============================================================

def get_klines(
    symbol,
    interval,
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
            "%s %s verisi alınamadı: %s",
            symbol,
            interval,
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

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    if avg_loss.iloc[-1] == 0:

        return 100.0

    rs = (
        avg_gain.iloc[-1]
        /
        avg_loss.iloc[-1]
    )

    return float(
        100 -
        (
            100 /
            (1 + rs)
        )
    )


# ============================================================
# YÜZDE HESABI
# ============================================================

def percent_change(
    old,
    new
):

    if old <= 0:

        return 0.0

    return (
        (
            new - old
        )
        /
        old
    ) * 100


# ============================================================
# COIN ANALİZİ
# ============================================================

def analyze_coin(
    symbol
):

    # --------------------------------------------------------
    # 5 DAKİKALIK VERİ
    # --------------------------------------------------------

    candles_5m = get_klines(
        symbol,
        "5m",
        100
    )

    if len(candles_5m) < 30:

        return None

    # --------------------------------------------------------
    # 15 DAKİKALIK VERİ
    # --------------------------------------------------------

    candles_15m = get_klines(
        symbol,
        "15m",
        100
    )

    if len(candles_15m) < 60:

        return None

    try:

        # Son kapanmamış mumları kullanma
        candles_5m = candles_5m[:-1]
        candles_15m = candles_15m[:-1]

        # ====================================================
        # 5M VERİ
        # ====================================================

        close_5m = [
            float(c[4])
            for c in candles_5m
        ]

        volume_5m = [
            float(c[6])
            for c in candles_5m
        ]

        taker_buy_5m = [
            float(c[9])
            for c in candles_5m
        ]

        # ====================================================
        # 15M VERİ
        # ====================================================

        close_15m = [
            float(c[4])
            for c in candles_15m
        ]

        volume_15m = [
            float(c[6])
            for c in candles_15m
        ]

        taker_buy_15m = [
            float(c[9])
            for c in candles_15m
        ]

        # ====================================================
        # FİYAT
        # ====================================================

        price = close_5m[-1]

        # ====================================================
        # 5M MOMENTUM
        # ====================================================

        momentum_5m = percent_change(
            close_5m[-2],
            close_5m[-1]
        )

        momentum_15m = percent_change(
            close_5m[-4],
            close_5m[-1]
        )

        momentum_30m = percent_change(
            close_5m[-7],
            close_5m[-1]
        )

        momentum_60m = percent_change(
            close_5m[-13],
            close_5m[-1]
        )

        # ====================================================
        # 5M HACİM
        # ====================================================

        current_volume = volume_5m[-1]

        average_volume = (
            sum(volume_5m[-25:-1])
            /
            len(volume_5m[-25:-1])
        )

        if average_volume <= 0:

            return None

        volume_ratio = (
            current_volume
            /
            average_volume
        )

        volume_change = percent_change(
            volume_5m[-2],
            current_volume
        )

        # ====================================================
        # HACİM İVME
        # ====================================================

        recent_volume = (
            sum(volume_5m[-3:])
            /
            3
        )

        previous_volume = (
            sum(volume_5m[-6:-3])
            /
            3
        )

        if previous_volume > 0:

            volume_acceleration = (
                (
                    recent_volume
                    -
                    previous_volume
                )
                /
                previous_volume
            ) * 100

        else:

            volume_acceleration = 0

        # ====================================================
        # ALICI BASKISI 5M
        # ====================================================

        if current_volume <= 0:

            return None

        buy_pressure = (
            taker_buy_5m[-1]
            /
            current_volume
        ) * 100

        # Son 3 mum ortalaması
        pressures = []

        for i in range(-3, 0):

            volume = volume_5m[i]

            if volume <= 0:
                continue

            pressure = (
                taker_buy_5m[i]
                /
                volume
            ) * 100

            pressures.append(
                pressure
            )

        if pressures:

            average_pressure = (
                sum(pressures)
                /
                len(pressures)
            )

        else:

            average_pressure = 0

        # ====================================================
        # 15M RSI
        # ====================================================

        rsi = calculate_rsi(
            close_15m
        )

        if rsi is None:

            return None

        # ====================================================
        # EMA
        # ====================================================

        series = pd.Series(
            close_15m,
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
        # 15M HACİM TRENDİ
        # ====================================================

        current_15m_volume = (
            volume_15m[-1]
        )

        average_15m_volume = (
            sum(volume_15m[-21:-1])
            /
            len(volume_15m[-21:-1])
        )

        if average_15m_volume > 0:

            volume_15m_ratio = (
                current_15m_volume
                /
                average_15m_volume
            )

        else:

            volume_15m_ratio = 0

        # ====================================================
        # PUAN
        # ====================================================

        score = 0

        reasons = []

        # ----------------------------------------------------
        # 5M HACİM
        # ----------------------------------------------------

        if volume_ratio >= 4:

            score += 25

            reasons.append(
                "🚀 5 dk hacim normalin 4 katından fazla"
            )

        elif volume_ratio >= 3:

            score += 20

            reasons.append(
                "🔥 5 dk hacim 3 katın üzerinde"
            )

        elif volume_ratio >= 2:

            score += 15

            reasons.append(
                "📈 5 dk hacim 2 katın üzerinde"
            )

        elif volume_ratio >= 1.5:

            score += 8

            reasons.append(
                "🟡 5 dk hacim yükseliyor"
            )

        # ----------------------------------------------------
        # HACİM HIZI
        # ----------------------------------------------------

        if volume_change >= 100:

            score += 15

            reasons.append(
                "⚡ Hacim son mumda patladı"
            )

        elif volume_change >= 50:

            score += 10

            reasons.append(
                "⚡ Hacim hızlanıyor"
            )

        elif volume_change >= 25:

            score += 5

            reasons.append(
                "📈 Hacim artış eğiliminde"
            )

        # ----------------------------------------------------
        # HACİM İVME
        # ----------------------------------------------------

        if volume_acceleration >= 100:

            score += 10

            reasons.append(
                "🔥 Hacim ivmesi çok güçlü"
            )

        elif volume_acceleration >= 50:

            score += 6

            reasons.append(
                "📈 Hacim ivmesi yükseliyor"
            )

        # ----------------------------------------------------
        # ALICI BASKISI
        # ----------------------------------------------------

        if buy_pressure >= 68:

            score += 20

            reasons.append(
                "🐋 Çok güçlü alıcı baskısı"
            )

        elif buy_pressure >= 63:

            score += 15

            reasons.append(
                "🟢 Güçlü alıcı baskısı"
            )

        elif buy_pressure >= 58:

            score += 10

            reasons.append(
                "🟢 Alıcı baskısı yükseliyor"
            )

        elif buy_pressure >= 54:

            score += 5

            reasons.append(
                "🟡 Alıcılar hafif üstün"
            )

        # ----------------------------------------------------
        # SON 3 MUM
        # ----------------------------------------------------

        if average_pressure >= 60:

            score += 10

            reasons.append(
                "🐋 Son 3 mumda güçlü alıcı baskısı"
            )

        elif average_pressure >= 55:

            score += 5

            reasons.append(
                "📈 Son 3 mumda alıcı baskısı pozitif"
            )

        # ----------------------------------------------------
        # 5M MOMENTUM
        # ----------------------------------------------------

        if 0.3 <= momentum_5m <= 2:

            score += 10

            reasons.append(
                "🟢 5 dk fiyat hareketi yeni başlıyor"
            )

        elif 2 < momentum_5m <= 4:

            score += 6

            reasons.append(
                "📈 5 dk momentum güçleniyor"
            )

        elif momentum_5m > 6:

            score -= 5

            reasons.append(
                "⚠️ Kısa vadeli hareket hızlandı"
            )

        # ----------------------------------------------------
        # 15M MOMENTUM
        # ----------------------------------------------------

        if 0 < momentum_15m < 4:

            score += 8

            reasons.append(
                "🟢 15 dk hareket henüz erken aşamada"
            )

        elif 4 <= momentum_15m < 8:

            score += 4

            reasons.append(
                "📈 15 dk momentum başladı"
            )

        elif momentum_15m >= 10:

            score -= 10

            reasons.append(
                "⚠️ 15 dk hareket fazla ilerledi"
            )

        # ----------------------------------------------------
        # 30M MOMENTUM
        # ----------------------------------------------------

        if 0 < momentum_30m < 7:

            score += 5

            reasons.append(
                "📈 30 dk trend kontrollü yükseliyor"
            )

        elif momentum_30m >= 12:

            score -= 8

            reasons.append(
                "⚠️ 30 dk hareket çok yükselmiş"
            )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if 45 <= rsi <= 62:

            score += 10

            reasons.append(
                "📊 RSI erken hareket için uygun"
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

        # ----------------------------------------------------
        # EMA TREND
        # ----------------------------------------------------

        if trend_up:

            score += 8

            reasons.append(
                "📈 15 dk EMA trendi yukarı"
            )

        # ----------------------------------------------------
        # 15M HACİM TEYİDİ
        # ----------------------------------------------------

        if volume_15m_ratio >= 2:

            score += 7

            reasons.append(
                "🔥 15 dk hacim de destekliyor"
            )

        elif volume_15m_ratio >= 1.5:

            score += 4

            reasons.append(
                "📈 15 dk hacim destekliyor"
            )

        # ====================================================
        # SKOR SINIRI
        # ====================================================

        score = max(
            0,
            min(
                score,
                100
            )
        )

        # ====================================================
        # GEÇ KALMIŞ HAREKET FİLTRESİ
        # ====================================================

        if momentum_30m >= 15:

            logger.info(
                "%s geç hareket filtresinden geçti.",
                symbol
            )

            return None

        if momentum_60m >= 25:

            return None

        # ====================================================
        # SİNYAL SEVİYESİ
        # ====================================================

        if score >= WHALE_SCORE:

            signal_type = (
                "🚨 ÇOK GÜÇLÜ BALİNA HAREKETİ"
            )

        elif score >= STRONG_SCORE:

            signal_type = (
                "🟢 GÜÇLÜ ERKEN GİRİŞ ADAYI"
            )

        elif score >= EARLY_SCORE:

            signal_type = (
                "🟡 ERKEN HAREKET UYARISI"
            )

        elif score >= WATCH_SCORE:

            signal_type = (
                "👀 TAKİP LİSTESİ"
            )

        else:

            return None

        return {

            "type": signal_type,

            "score": score,

            "price": price,

            "rsi": round(
                rsi,
                1
            ),

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
               
