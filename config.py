from __future__ import annotations

import os
from dataclasses import dataclass, field


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} geçerli bir tam sayı değil: {value!r}") from exc


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} geçerli bir sayı değil: {value!r}") from exc


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    base_url: str = field(
        default_factory=lambda: env_str(
            "BINANCE_TR_BASE",
            "https://api.binance.me",
        )
    )

    scan_interval: int = field(
        default_factory=lambda: env_int("SCAN_INTERVAL", 30)
    )
    workers: int = field(
        default_factory=lambda: env_int("MAX_WORKERS", 8)
    )
    shortlist_size: int = field(
        default_factory=lambda: env_int("SHORTLIST_SIZE", 60)
    )
    max_signals: int = field(
        default_factory=lambda: env_int("MAX_SIGNALS_PER_SCAN", 3)
    )

    request_timeout: int = field(
        default_factory=lambda: env_int("REQUEST_TIMEOUT", 8)
    )

    db_path: str = field(
        default_factory=lambda: env_str("STATE_DB_PATH", "balina_v24.db")
    )

    outcome_window: int = field(
        default_factory=lambda: env_int("OUTCOME_WINDOW", 900)
    )
    signal_retention_days: int = field(
        default_factory=lambda: env_int("SIGNAL_RETENTION_DAYS", 30)
    )

    telegram_token: str = field(
        default_factory=lambda: env_str("TELEGRAM_BOT_TOKEN")
    )
    telegram_chat: str = field(
        default_factory=lambda: env_str("TELEGRAM_CHAT_ID")
    )
    admin_token: str = field(
        default_factory=lambda: env_str("ADMIN_TOKEN")
    )

    min_quote_volume: float = field(
        default_factory=lambda: env_float(
            "MIN_QUOTE_VOLUME_TRY",
            1_000_000,
        )
    )

    cooldown: int = field(
        default_factory=lambda: env_int("SIGNAL_COOLDOWN", 1200)
    )

    min_priority: float = field(
        default_factory=lambda: env_float("MIN_PRIORITY", 60)
    )

    market_symbol: str = field(
        default_factory=lambda: env_str("MARKET_SYMBOL", "BTCTRY")
    )
    market_move: float = field(
        default_factory=lambda: env_float("MARKET_MOVE", 2.0)
    )
    market_cache_ttl: int = field(
        default_factory=lambda: env_int("MARKET_CACHE_TTL", 60)
    )
    daily_cache_ttl: int = field(
        default_factory=lambda: env_int("DAILY_CACHE_TTL", 900)
    )

    min_1m_trades: int = field(
        default_factory=lambda: env_int("MIN_1M_TRADES", 20)
    )
    min_5m_trades: int = field(
        default_factory=lambda: env_int("MIN_5M_TRADES", 50)
    )
    trade_reference: int = field(
        default_factory=lambda: env_int("TRADE_REFERENCE", 100)
    )

    streak_window: int = field(
        default_factory=lambda: env_int("STREAK_WINDOW", 180)
    )
    buy_streak: int = field(
        default_factory=lambda: env_int("BUY_STREAK", 2)
    )
    very_streak: int = field(
        default_factory=lambda: env_int("VERY_STREAK", 2)
    )

    lt30_mild: float = field(
        default_factory=lambda: env_float("LT30_MILD", -20)
    )
    lt30_strong: float = field(
        default_factory=lambda: env_float("LT30_STRONG", -35)
    )
    lt90_mild: float = field(
        default_factory=lambda: env_float("LT90_MILD", -30)
    )
    lt90_strong: float = field(
        default_factory=lambda: env_float("LT90_STRONG", -50)
    )
    lt90_extreme: float = field(
        default_factory=lambda: env_float("LT90_EXTREME", -65)
    )

    trap_buyer: float = field(
        default_factory=lambda: env_float("TRAP_BUYER", 50)
    )
    trap_volume: float = field(
        default_factory=lambda: env_float("TRAP_VOLUME", 1.8)
    )
    trap_momentum: float = field(
        default_factory=lambda: env_float("TRAP_MOMENTUM", -1.2)
    )

    weight_budget_per_minute: int = field(
        default_factory=lambda: env_int(
            "WEIGHT_BUDGET_PER_MINUTE",
            900,
        )
    )

    excluded_symbols: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "USDTTRY",
                "USDCUSDT",
                "FDUSDUSDT",
                "TUSDUSDT",
                "BUSDUSDT",
                "DAIUSDT",
            }
        )
    )

    def validate(self) -> None:
        errors: list[str] = []

        if not self.base_url.startswith(("http://", "https://")):
            errors.append("BINANCE_TR_BASE geçerli bir URL olmalı")

        if self.scan_interval < 10:
            errors.append("SCAN_INTERVAL en az 10 olmalı")

        if self.workers < 1 or self.workers > 32:
            errors.append("MAX_WORKERS 1-32 arasında olmalı")

        if self.shortlist_size < 1 or self.shortlist_size > 200:
            errors.append("SHORTLIST_SIZE 1-200 arasında olmalı")

        if self.max_signals < 1 or self.max_signals > 20:
            errors.append("MAX_SIGNALS_PER_SCAN 1-20 arasında olmalı")

        if self.request_timeout < 2:
            errors.append("REQUEST_TIMEOUT en az 2 olmalı")

        if self.min_quote_volume <= 0:
            errors.append("MIN_QUOTE_VOLUME_TRY sıfırdan büyük olmalı")

        if self.cooldown < 0:
            errors.append("SIGNAL_COOLDOWN negatif olamaz")

        if not 0 <= self.min_priority <= 100:
            errors.append("MIN_PRIORITY 0-100 arasında olmalı")

        if self.outcome_window < 900:
            errors.append("OUTCOME_WINDOW en az 900 olmalı")

        if self.signal_retention_days < 1:
            errors.append("SIGNAL_RETENTION_DAYS en az 1 olmalı")

        if self.daily_cache_ttl < 30:
            errors.append("DAILY_CACHE_TTL en az 30 olmalı")

        if self.market_cache_ttl < 10:
            errors.append("MARKET_CACHE_TTL en az 10 olmalı")

        if self.min_1m_trades < 1:
            errors.append("MIN_1M_TRADES en az 1 olmalı")

        if self.min_5m_trades < 1:
            errors.append("MIN_5M_TRADES en az 1 olmalı")

        if self.trade_reference < 1:
            errors.append("TRADE_REFERENCE en az 1 olmalı")

        if self.streak_window < 30:
            errors.append("STREAK_WINDOW en az 30 olmalı")

        if self.buy_streak < 1:
            errors.append("BUY_STREAK en az 1 olmalı")

        if self.very_streak < self.buy_streak:
            errors.append("VERY_STREAK BUY_STREAK'ten küçük olamaz")

        if self.lt30_strong > self.lt30_mild:
            errors.append("LT30_STRONG, LT30_MILD'den küçük/eşit olmalı")

        if self.lt90_extreme > self.lt90_strong:
            errors.append("LT90_EXTREME, LT90_STRONG'dan küçük/eşit olmalı")

        if self.lt90_strong > self.lt90_mild:
            errors.append("LT90_STRONG, LT90_MILD'den küçük/eşit olmalı")

        if self.trap_volume <= 0:
            errors.append("TRAP_VOLUME sıfırdan büyük olmalı")

        if self.weight_budget_per_minute < 100:
            errors.append("WEIGHT_BUDGET_PER_MINUTE en az 100 olmalı")

        if errors:
            raise ValueError(
                "Konfigürasyon hatası:\n- " + "\n- ".join(errors)
            )


SETTINGS = Settings()
SETTINGS.validate()
