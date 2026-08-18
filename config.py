import os
from dataclasses import dataclass


def get_int(name, default):
    try:
        return int(os.getenv(name, default))
    except:
        return default


def get_float(name, default):
    try:
        return float(os.getenv(name, default))
    except:
        return default


@dataclass
class Settings:

    base_url: str = os.getenv(
        "BINANCE_API_URL",
        "https://api.binance.me"
    )

    telegram_token: str = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        ""
    )

    telegram_chat: str = os.getenv(
        "TELEGRAM_CHAT_ID",
        ""
    )

    scan_interval: int = get_int(
        "SCAN_INTERVAL",
        60
    )

    workers: int = get_int(
        "MAX_WORKERS",
        6
    )

    shortlist: int = get_int(
        "SHORTLIST_SIZE",
        40
    )

    min_volume: float = get_float(
        "MIN_VOLUME_TRY",
        1000000
    )

    max_signals: int = get_int(
        "MAX_SIGNALS",
        3
    )

    cooldown: int = get_int(
        "SIGNAL_COOLDOWN",
        1800
    )

    candles: int = get_int(
        "CANDLE_LIMIT",
        300
    )

    fib_tolerance: float = get_float(
        "FIB_TOLERANCE",
        0.8
    )

    profile_bins: int = get_int(
        "PROFILE_BINS",
        50
    )

    profile_value_area: float = get_float(
        "PROFILE_VALUE_AREA",
        70
    )

    ichimoku_conversion: int = get_int(
        "ICHIMOKU_CONVERSION",
        20
    )

    ichimoku_base: int = get_int(
        "ICHIMOKU_BASE",
        60
    )

    ichimoku_span: int = get_int(
        "ICHIMOKU_SPAN",
        120
    )

    ichimoku_displacement: int = get_int(
        "ICHIMOKU_DISPLACEMENT",
        30
    )

    rsi_min: float = get_float(
        "RSI_MIN",
        30
    )

    rsi_max: float = get_float(
        "RSI_MAX",
        70
    )

    volume_ratio: float = get_float(
        "VOLUME_RATIO",
        1.5
    )

    td_setup: int = get_int(
        "TD_SETUP",
        9
    )

    td_countdown: int = get_int(
        "TD_COUNTDOWN",
        13
    )


SETTINGS = Settings()
