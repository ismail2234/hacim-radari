import os
from dataclasses import dataclass


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except:
        return default


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except:
        return default


@dataclass
class Settings:

    base_url: str = os.getenv(
        "BINANCE_TR_BASE",
        "https://api.binance.me",
    )

    scan_interval: int = env_int(
        "SCAN_INTERVAL",
        60,
    )

    workers: int = env_int(
        "MAX_WORKERS",
        8,
    )

    max_signals: int = env_int(
        "MAX_SIGNALS_PER_SCAN",
        3,
    )

    min_volume: float = env_float(
        "MIN_QUOTE_VOLUME_TRY",
        1000000,
    )

    shortlist: int = env_int(
        "SHORTLIST_SIZE",
        50,
    )

    candles: int = env_int(
        "CANDLES",
        200,
    )

    timeout: int = env_int(
        "REQUEST_TIMEOUT",
        10,
    )

    telegram_token: str = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        "",
    )

    telegram_chat: str = os.getenv(
        "TELEGRAM_CHAT_ID",
        "",
    )

    ichimoku_conversion: int = 20
    ichimoku_base: int = 60
    ichimoku_span: int = 120
    ichimoku_displacement: int = 30

    profile_bins: int = 50
    profile_value_area: float = 70.0

    fib_tolerance: float = 0.50

    volume_ratio: float = 1.50

    td_buy: int = 9
    td_strong: int = 13

    min_score_buy: int = 70
    min_score_strong: int = 85


SETTINGS = Settings()
