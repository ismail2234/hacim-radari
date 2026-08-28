from __future__ import annotations

import os


# ============================================================
# V30 GENEL AYARLAR
# ============================================================

APP_NAME = "Hacim Radarı V30"
VERSION = "V30"

# Binance TR
BINANCE_BASE_URL = os.getenv(
    "BINANCE_BASE_URL",
    "https://api.binance.com",
)

# 5 dakikalık mum
INTERVAL = os.getenv(
    "INTERVAL",
    "5m",
)

# Her coin için alınacak mum sayısı
CANDLE_LIMIT = int(
    os.getenv(
        "CANDLE_LIMIT",
        "100",
    )
)

# Canlı tarama aralığı
SCAN_SECONDS = int(
    os.getenv(
        "SCAN_SECONDS",
        "60",
    )
)


# ============================================================
# SİNYAL AYARLARI
# ============================================================

# Minimum sinyal skoru
MIN_BUY_SCORE = float(
    os.getenv(
        "MIN_BUY_SCORE",
        "75",
    )
)

# Coinin zaten çok yükselmiş olması durumunda
# geç sinyali engellemek için maksimum fiyat hareketi.
MAX_LATE_MOVE_PERCENT = float(
    os.getenv(
        "MAX_LATE_MOVE_PERCENT",
        "6.0",
    )
)


# ============================================================
# HACİM AYARLARI
# ============================================================

# Çok küçük hacimli pariteleri filtrelemek için
MIN_VOLUME_TRY = float(
    os.getenv(
        "MIN_VOLUME_TRY",
        "10000",
    )
)

# Tek mumluk aşırı hacim patlamalarını
# doğrudan AL sinyali olarak kabul etmeyeceğiz.
MAX_SINGLE_CANDLE_VOLUME_SPIKE = float(
    os.getenv(
        "MAX_SINGLE_CANDLE_VOLUME_SPIKE",
        "5.0",
    )
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
)


# ============================================================
# DEBUG
# ============================================================

DEBUG = os.getenv(
    "DEBUG",
    "false",
).lower() == "true"
