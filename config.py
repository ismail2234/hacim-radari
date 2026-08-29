from __future__ import annotations

import os


APP_NAME = "Hacim Radarı V30"
VERSION = "V30"

# Binance TR genel API
BINANCE_TR_BASE_URL = os.getenv(
    "BINANCE_TR_BASE_URL",
    "https://www.binance.tr",
)

# Binance TR MAIN (type 1) market-data endpoint'i
BINANCE_MARKET_BASE_URL = os.getenv(
    "BINANCE_MARKET_BASE_URL",
    "https://api.binance.me",
)

INTERVAL = os.getenv(
    "INTERVAL",
    "5m",
)

CANDLE_LIMIT = int(
    os.getenv(
        "CANDLE_LIMIT",
        "100",
    )
)

SCAN_SECONDS = int(
    os.getenv(
        "SCAN_SECONDS",
        "60",
    )
)


# ============================================================
# SİNYAL
# ============================================================

MIN_BUY_SCORE = float(
    os.getenv(
        "MIN_BUY_SCORE",
        "75",
    )
)

MAX_LATE_MOVE_PERCENT = float(
    os.getenv(
        "MAX_LATE_MOVE_PERCENT",
        "6.0",
    )
)


# ============================================================
# HACİM
# ============================================================

MIN_VOLUME_TRY = float(
    os.getenv(
        "MIN_VOLUME_TRY",
        "10000",
    )
)

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


DEBUG = os.getenv(
    "DEBUG",
    "false",
).lower() == "true"
