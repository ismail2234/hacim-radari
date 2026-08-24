import os
from dotenv import load_dotenv

load_dotenv()


def get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} geçerli bir tam sayı olmalı.")


def get_env_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{name} geçerli bir sayı olmalı.")


# Uygulama
APP_NAME = os.getenv("APP_NAME", "Hacim Radarı")
PORT = get_env_int("PORT", 5000)

# Binance TR
BINANCE_TR_BASE_URL = os.getenv(
    "BINANCE_TR_BASE_URL",
    "https://api.binance.tr"
)

# Tarama
SCAN_INTERVAL = get_env_int("SCAN_INTERVAL", 60)

# Minimum TL hacim
MIN_VOLUME_TRY = get_env_float("MIN_VOLUME_TRY", 100000.0)

# Sinyal ayarları
MIN_SIGNAL_SCORE = get_env_int("MIN_SIGNAL_SCORE", 70)
