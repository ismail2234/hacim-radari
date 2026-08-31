from __future__ import annotations

import os

APP_NAME = "Hacim Radarı V33"
VERSION = "V33"

BINANCE_TR_BASE_URL = os.getenv(
    "BINANCE_TR_BASE_URL",
    "https://www.binance.tr",
)

BINANCE_MARKET_BASE_URL = os.getenv(
    "BINANCE_MARKET_BASE_URL",
    "https://api.binance.me",
)

INTERVAL = os.getenv("INTERVAL", "5m")
CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "300"))
SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "300"))

MIN_BUY_SCORE = float(os.getenv("MIN_BUY_SCORE", "78"))
MAX_LATE_MOVE_PERCENT = float(os.getenv("MAX_LATE_MOVE_PERCENT", "1.50"))
MIN_VOLUME_TRY = float(os.getenv("MIN_VOLUME_TRY", "10000"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
