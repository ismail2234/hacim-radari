from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name}='{value}' geçerli bir tam sayı değil"
        ) from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"{name}='{value}' geçerli bir sayı değil"
        ) from exc


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return default

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on", "evet"}:
        return True

    if normalized in {"0", "false", "no", "off", "hayir", "hayır"}:
        return False

    raise ValueError(
        f"{name}='{value}' geçerli bir boolean değil"
    )


@dataclass(frozen=True)
class Settings:
    base_url: str = field(
        default_factory=lambda: _env_str(
            "BINANCE_TR_BASE",
            "https://api.binance.me",
        )
    )

    scan_interval: int = field(
        default_factory=lambda: _env_int("SCAN_INTERVAL", 30)
    )

    workers: int = field(
        default_factory=lambda: _env_int("MAX_WORKERS", 10)
    )

    max_signals: int = field(
        default_factory=lambda: _env_int(
            "MAX_SIGNALS_PER_SCAN",
            3,
        )
    )

    cooldown: int = field(
        default_factory=lambda: _env_int(
            "SIGNAL_COOLDOWN",
            1200,
        )
    )

    min_quote_volume: float = field(
        default_factory=lambda: _env_float(
            "MIN_QUOTE_VOLUME_TRY",
            1_000_000,
        )
    )

    shortlist_size: int = field(
        default_factory=lambda: _env_int(
            "SHORTLIST_SIZE",
            80,
        )
    )

    request_timeout: int = field(
        default_factory=lambda: _env_int(
            "REQUEST_TIMEOUT",
            8,
        )
    )

    db_path: str = field(
        default_factory=lambda: _env_str(
            "STATE_DB_PATH",
            "balina_v24.db",
        )
    )

    outcome_window: int = field(
        default_factory=lambda: _env_int(
            "OUTCOME_WINDOW",
            900,
        )
    )

    signal_retention_days: int = field(
        default_factory=lambda: _env_int(
            "SIGNAL_RETENTION_DAYS",
            30,
        )
    )

    telegram_token: str = field(
        default_factory=lambda: _env_str(
            "TELEGRAM_BOT_TOKEN",
            "",
        )
    )

    telegram_chat: str = field(
        default_factory=lambda: _env_str(
            "TELEGRAM_CHAT_ID",
            "",
        )
    )

    lt30_mild: float = field(
        default_factory=lambda: _env_float(
            "LT30_MILD",
            -20,
        )
    )

    lt30_strong: float = field(
        default_factory=lambda: _env_float(
            "LT30_STRONG",
            -35,
        )
    )

    lt90_mild: float = field(
        default_factory=lambda: _env_float(
            "LT90_MILD",
            -30,
        )
    )

    lt90_strong: float = field(
        default_factory=lambda: _env_float(
            "LT90_STRONG",
            -50,
        )
    )

    lt90_extreme: float = field(
        default_factory=lambda: _env_float(
            "LT90_EXTREME",
            -65,
        )
    )

    daily_cache_ttl: int = field(
        default_factory=lambda: _env_int(
            "DAILY_CACHE_TTL",
            900,
        )
    )

    min_1m_trades: int = field(
        default_factory=lambda: _env_int(
            "MIN_1M_TRADES",
            20,
        )
    )

    min_5m_trades: int = field(
        default_factory=lambda: _env_int(
            "MIN_5M_TRADES",
            50,
        )
    )

    trade_reference: int = field(
        default_factory=lambda: _env_int(
            "TRADE_REFERENCE",
            100,
        )
    )

    streak_window: int = field(
        default_factory=lambda: _env_int(
            "STREAK_WINDOW",
            180,
        )
    )

    buy_streak: int = field(
        default_factory=lambda: _env_int(
            "BUY_STREAK",
            2,
        )
    )

    very_streak: int = field(
        default_factory=lambda: _env_int(
            "VERY_STREAK",
            2,
        )
    )

    market_symbol: str = field(
        default_factory=lambda: _env_str(
            "MARKET_SYMBOL",
            "BTCTRY",
        )
    )

    market_move: float = field(
        default_factory=lambda: _env_float(
            "MARKET_MOVE",
            2.0,
        )
    )

    top_priority: int = field(
        default_factory=lambda: _env_int(
            "TOP_PRIORITY",
            5,
        )
    )

    min_priority: int = field(
        default_factory=lambda: _env_int(
            "MIN_PRIORITY",
            60,
        )
    )

    trap_buyer: float = field(
        default_factory=lambda: _env_float(
            "TRAP_BUYER",
            50,
        )
    )

    trap_volume: float = field(
        default_factory=lambda: _env_float(
            "TRAP_VOLUME",
            1.8,
        )
    )

    trap_momentum: float = field(
        default_factory=lambda: _env_float(
            "TRAP_MOMENTUM",
            -1.2,
        )
    )

    weight_budget_per_minute: int = field(
        default_factory=lambda: _env_int(
            "WEIGHT_BUDGET_PER_MINUTE",
            1000,
        )
    )

    admin_token: str = field(
        default_factory=lambda: _env_str(
            "ADMIN_TOKEN",
            "",
        )
    )

    debug: bool = field(
        default_factory=lambda: _env_bool(
            "DEBUG",
            False,
        )
    )

    excluded_symbols: frozenset[str] = field(
        default_factory=lambda: frozenset({
            "USDTTRY",
            "USDCUSDT",
            "FDUSDUSDT",
            "TUSDUSDT",
            "BUSDUSDT",
            "DAIUSDT",
        })
    )

    def validate(self) -> None:
        problems: list[str] = []

        if not self.base_url:
            problems.append("BINANCE_TR_BASE boş olamaz")

        if self.workers < 1:
            problems.append("MAX_WORKERS >= 1 olmalı")

        if self.workers > 30:
            problems.append("MAX_WORKERS gereksiz derecede yüksek")

        if self.scan_interval < 5:
            problems.append("SCAN_INTERVAL >= 5 olmalı")

        if self.max_signals < 1:
            problems.append(
                "MAX_SIGNALS_PER_SCAN >= 1 olmalı"
            )

        if self.cooldown < 0:
            problems.append("SIGNAL_COOLDOWN >= 0 olmalı")

        if self.min_quote_volume < 0:
            problems.append(
                "MIN_QUOTE_VOLUME_TRY >= 0 olmalı"
            )

        if self.shortlist_size < 1:
            problems.append("SHORTLIST_SIZE >= 1 olmalı")

        if self.shortlist_size > 500:
            problems.append(
                "SHORTLIST_SIZE 500'den büyük olmamalı"
            )

        if self.request_timeout < 1:
            problems.append("REQUEST_TIMEOUT >= 1 olmalı")

        if self.outcome_window < 60:
            problems.append("OUTCOME_WINDOW >= 60 olmalı")

        if self.signal_retention_days < 1:
            problems.append(
                "SIGNAL_RETENTION_DAYS >= 1 olmalı"
            )

        if self.daily_cache_ttl < 1:
            problems.append("DAILY_CACHE_TTL >= 1 olmalı")

        if self.min_1m_trades < 0:
            problems.append("MIN_1M_TRADES >= 0 olmalı")

        if self.min_5m_trades < 0:
            problems.append("MIN_5M_TRADES >= 0 olmalı")

        if self.trade_reference <= 0:
            problems.append("TRADE_REFERENCE > 0 olmalı")

        if self.streak_window <= 0:
            problems.append("STREAK_WINDOW > 0 olmalı")

        if self.buy_streak < 1:
            problems.append("BUY_STREAK >= 1 olmalı")

        if self.very_streak < 1:
            problems.append("VERY_STREAK >= 1 olmalı")

        if not self.market_symbol:
            problems.append("MARKET_SYMBOL boş olamaz")

        if self.market_move <= 0:
            problems.append("MARKET_MOVE > 0 olmalı")

        if not 0 <= self.min_priority <= 100:
            problems.append(
                "MIN_PRIORITY 0 ile 100 arasında olmalı"
            )

        if not 1 <= self.top_priority <= 100:
            problems.append(
                "TOP_PRIORITY 1 ile 100 arasında olmalı"
            )

        if self.weight_budget_per_minute < 100:
            problems.append(
                "WEIGHT_BUDGET_PER_MINUTE >= 100 olmalı"
            )

        if not 0 <= self.trap_buyer <= 100:
            problems.append(
                "TRAP_BUYER 0 ile 100 arasında olmalı"
            )

        if self.trap_volume <= 0:
            problems.append("TRAP_VOLUME > 0 olmalı")

        if self.lt30_strong > self.lt30_mild:
            problems.append(
                "LT30_STRONG, LT30_MILD'den küçük/eşit olmalı"
            )

        if self.lt90_extreme > self.lt90_strong:
            problems.append(
                "LT90_EXTREME, LT90_STRONG'dan küçük/eşit olmalı"
            )

        if self.lt90_strong > self.lt90_mild:
            problems.append(
                "LT90_STRONG, LT90_MILD'den küçük/eşit olmalı"
            )

        if self.top_priority > self.shortlist_size:
            problems.append(
                "TOP_PRIORITY, SHORTLIST_SIZE'dan büyük olamaz"
            )

        if problems:
            raise ValueError(
                "Konfigürasyon hatası:\n- "
                + "\n- ".join(problems)
            )


SETTINGS = Settings()
