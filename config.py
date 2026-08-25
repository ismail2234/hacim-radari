from __future__ import annotations

import os


# ============================================================
# BINANCE TR
# ============================================================

# Railway Variables üzerinden değiştirilebilir.
# Örnek:
# BINANCE_TR_BASE_URL=https://api.binance.tr

BINANCE_TR_BASE_URL = os.getenv(
    "BINANCE_TR_BASE_URL",
    "https://www.binance.tr"
).rstrip("/")


# ============================================================
# MARKET SCANNER
# ============================================================

SCAN_INTERVAL = os.getenv(
    "SCAN_INTERVAL",
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
# V29
# ============================================================

V29_VERSION = "V29"


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "10",
    )
)


# ============================================================
# TELEGRAM
# ============================================================

# Şimdilik boş bırakıyoruz.
# Telegram'ı sonraki aşamada bağlayacağız.

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
)


# ============================================================
# LOG
# ============================================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
)
