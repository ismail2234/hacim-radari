from __future__ import annotations

import os
from dataclasses import dataclass, field


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_bool(name, default=False):
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@dataclass
class Settings:

    base_url: str = field(
        default_factory=lambda:
        os.getenv(
            "BINANCE_BASE_URL",
            "https://api.binance.com",
        )
    )

    telegram_token: str = field(
        default_factory=lambda:
        os.getenv(
            "TELEGRAM_BOT_TOKEN",
            "",
        )
    )

    telegram_chat: str = field(
        default_factory=lambda:
        os.getenv(
            "TELEGRAM_CHAT_ID",
            "",
        )
    )

    db_path: str = field(
        default_factory=lambda:
        os.getenv(
            "DB_PATH",
            "balina.db",
        )
    )

    workers: int = field(
        default_factory=lambda:
        env_int(
            "WORKERS",
            4,
        )
    )

    scan_interval: int = field(
        default_factory=lambda:
        env_int(
            "SCAN_INTERVAL",
            60,
        )
    )

    shortlist_size: int = field(
        default_factory=lambda:
        env_int(
            "SHORTLIST_SIZE",
            20,
        )
    )

    max_signals: int = field(
        default_factory=lambda:
        env_int(
            "MAX_SIGNALS",
            5,
        )
    )

    min_quote_volume: float = field(
        default_factory=lambda:
        env_float(
            "MIN_QUOTE_VOLUME",
            100000,
        )
    )

    cooldown: int = field(
        default_factory=lambda:
        env_int(
            "COOLDOWN",
            900,
        )
    )

    candles: int = field(
        default_factory=lambda:
        env_int(
            "CANDLES",
            300,
        )
    )

    signal_retention_days: int = field(
        default_factory=lambda:
        env_int(
            "SIGNAL_RETENTION_DAYS",
            30,
        )
    )

    outcome_window: int = field(
        default_factory=lambda:
        env_int(
            "OUTCOME_WINDOW",
            3600,
        )
    )

    weight_budget_per_minute: int = field(
        default_factory=lambda:
        env_int(
            "WEIGHT_BUDGET_PER_MINUTE",
            1200,
        )
    )

    excluded_symbols: set[str] = field(
        default_factory=set
    )

    ichimoku_conversion: int = 20
    ichimoku_base: int = 60
    ichimoku_span: int = 120
    ichimoku_displacement: int = 30

    fibonacci_levels: tuple = (
        0.5,
        0.618,
        0.786,
    )

    volume_profile_bins: int = 50
    volume_profile_value_area: float = 70.0

    td_setup: int = 9
    td_countdown: int = 13

    rsi_period: int = 14
    adx_period: int = 14

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    volume_ratio_period: int = 20

    min_score: float = 60.0

    require_ichimoku: bool = True
    require_fib_poc: bool = False
    require_td9: bool = False

    def validate(self):

        if not self.base_url:
            raise ValueError(
                "BINANCE_BASE_URL eksik"
            )

        if not self.telegram_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN eksik"
            )

        if not self.telegram_chat:
            raise ValueError(
                "TELEGRAM_CHAT_ID eksik"
            )

        if self.workers < 1:
            self.workers = 1

        if self.scan_interval < 10:
            self.scan_interval = 10

        if self.shortlist_size < 1:
            self.shortlist_size = 1

        if self.max_signals < 1:
            self.max_signals = 1

        if self.candles < 150:
            self.candles = 150

        if self.volume_profile_bins < 10:
            self.volume_profile_bins = 10

        return True


SETTINGS = Settings()


EXCLUDED = os.getenv(
    "EXCLUDED_SYMBOLS",
    "",
).strip()

if EXCLUDED:

    SETTINGS.excluded_symbols = {
        x.strip().upper()
        for x in EXCLUDED.split(",")
        if x.strip()
    }
