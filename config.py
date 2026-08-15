from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name}='{raw}' geçerli bir tam sayı değil") from e


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name}='{raw}' geçerli bir sayı değil") from e


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    # --- Sistem ---
    base_url: str = field(default_factory=lambda: _env_str(
        "BINANCE_TR_BASE", "https://api.binance.me"
    ))
    scan_interval: int = field(default_factory=lambda: _env_int("SCAN_INTERVAL", 30))
    workers: int = field(default_factory=lambda: _env_int("MAX_WORKERS", 10))
    max_signals: int = field(default_factory=lambda: _env_int("MAX_SIGNALS_PER_SCAN", 3))
    cooldown: int = field(default_factory=lambda: _env_int("SIGNAL_COOLDOWN", 1200))
    min_quote_volume: float = field(default_factory=lambda: _env_float(
        "MIN_QUOTE_VOLUME_TRY", 1_000_000
    ))
    shortlist_size: int = field(default_factory=lambda: _env_int("SHORTLIST_SIZE", 80))
    request_timeout: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT", 8))

    # --- DB / performans ---
    db_path: str = field(default_factory=lambda: _env_str(
        "STATE_DB_PATH", "balina_v24.db"
    ))
    outcome_window: int = field(default_factory=lambda: _env_int("OUTCOME_WINDOW", 900))
    signal_retention_days: int = field(default_factory=lambda: _env_int(
        "SIGNAL_RETENTION_DAYS", 30
    ))

    # --- Telegram ---
    telegram_token: str = field(default_factory=lambda: _env_str("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat: str = field(default_factory=lambda: _env_str("TELEGRAM_CHAT_ID", ""))

    # --- Uzun vadeli risk: artık sinyal tier'ını doğrudan engellemek için değil,
    # Telegram'da risk rozeti göstermek için kullanılır. ---
    lt30_mild: float = field(default_factory=lambda: _env_float("LT30_MILD", -20))
    lt30_strong: float = field(default_factory=lambda: _env_float("LT30_STRONG", -35))
    lt90_mild: float = field(default_factory=lambda: _env_float("LT90_MILD", -30))
    lt90_strong: float = field(default_factory=lambda: _env_float("LT90_STRONG", -50))
    lt90_extreme: float = field(default_factory=lambda: _env_float("LT90_EXTREME", -65))

    daily_cache_ttl: int = field(default_factory=lambda: _env_int("DAILY_CACHE_TTL", 900))

    # --- İşlem / hacim ---
    min_1m_trades: int = field(default_factory=lambda: _env_int("MIN_1M_TRADES", 20))
    min_5m_trades: int = field(default_factory=lambda: _env_int("MIN_5M_TRADES", 50))
    trade_reference: int = field(default_factory=lambda: _env_int("TRADE_REFERENCE", 100))

    # Faz B: işlem ivmesinde çok küçük bölene karşı koruma.
    trade_accel_min_previous: int = field(default_factory=lambda: _env_int(
        "TRADE_ACCEL_MIN_PREVIOUS", 10
    ))
    trade_accel_ratio: float = field(default_factory=lambda: _env_float(
        "TRADE_ACCEL_RATIO", 1.20
    ))
    trade_accel_strong_ratio: float = field(default_factory=lambda: _env_float(
        "TRADE_ACCEL_STRONG_RATIO", 1.60
    ))

    # Faz B: streak artık sinyal kapısı değildir.
    streak_window: int = field(default_factory=lambda: _env_int("STREAK_WINDOW", 180))
    buy_streak: int = field(default_factory=lambda: _env_int("BUY_STREAK", 1))
    very_streak: int = field(default_factory=lambda: _env_int("VERY_STREAK", 2))

    # --- Faz B: erken hareket eşikleri ---
    # ÖNCÜ AL için RSI'nin sağlıklı/yükselen bölgesi.
    rsi_early_min: float = field(default_factory=lambda: _env_float("RSI_EARLY_MIN", 50))
    rsi_early_max: float = field(default_factory=lambda: _env_float("RSI_EARLY_MAX", 75))
    rsi_overheated: float = field(default_factory=lambda: _env_float("RSI_OVERHEATED", 80))
    rsi_extreme: float = field(default_factory=lambda: _env_float("RSI_EXTREME", 90))

    # ADX mutlak seviyesi ÖNCÜ AL için zorunlu değildir.
    # Güçlü trend için kullanılır.
    min_adx_trend: int = field(default_factory=lambda: _env_int("MIN_ADX_TREND", 25))
    early_adx_min: int = field(default_factory=lambda: _env_int("EARLY_ADX_MIN", 12))
    weak_adx_penalty: int = field(default_factory=lambda: _env_int("WEAK_ADX_PENALTY", 5))

    # Hacim ivmesi: 3 pencere ana kriterdir.
    volume_accel_min_ratio: float = field(default_factory=lambda: _env_float(
        "VOLUME_ACCEL_MIN_RATIO", 1.10
    ))
    volume_accel_strong_ratio: float = field(default_factory=lambda: _env_float(
        "VOLUME_ACCEL_STRONG_RATIO", 1.35
    ))
    volume_accel_early_ratio: float = field(default_factory=lambda: _env_float(
        "VOLUME_ACCEL_EARLY_RATIO", 1.80
    ))

    # Momentum ivmesi.
    momentum_accel_min: float = field(default_factory=lambda: _env_float(
        "MOMENTUM_ACCEL_MIN", 0.15
    ))
    momentum_accel_strong: float = field(default_factory=lambda: _env_float(
        "MOMENTUM_ACCEL_STRONG", 0.50
    ))

    # --- Trap ---
    trap_buyer: float = field(default_factory=lambda: _env_float("TRAP_BUYER", 50))
    trap_volume: float = field(default_factory=lambda: _env_float("TRAP_VOLUME", 1.8))
    trap_momentum: float = field(default_factory=lambda: _env_float("TRAP_MOMENTUM", -1.2))

    # --- Skor ---
    score_soft_cap: int = field(default_factory=lambda: _env_int("SCORE_SOFT_CAP", 80))
    score_soft_cap_factor: float = field(default_factory=lambda: _env_float(
        "SCORE_SOFT_CAP_FACTOR", 0.40
    ))

    priority_soft_cap: int = field(default_factory=lambda: _env_int("PRIORITY_SOFT_CAP", 75))
    priority_soft_cap_factor: float = field(default_factory=lambda: _env_float(
        "PRIORITY_SOFT_CAP_FACTOR", 0.40
    ))

    entry_soft_cap: int = field(default_factory=lambda: _env_int("ENTRY_SOFT_CAP", 78))
    entry_soft_cap_factor: float = field(default_factory=lambda: _env_float(
        "ENTRY_SOFT_CAP_FACTOR", 0.35
    ))

    top_priority: int = field(default_factory=lambda: _env_int("TOP_PRIORITY", 5))
    min_priority: int = field(default_factory=lambda: _env_int("MIN_PRIORITY", 60))

    # --- Market ---
    market_symbol: str = field(default_factory=lambda: _env_str("MARKET_SYMBOL", "BTCTRY"))
    market_move: float = field(default_factory=lambda: _env_float("MARKET_MOVE", 2))

    # --- API rate limit ---
    weight_budget_per_minute: int = field(default_factory=lambda: _env_int(
        "WEIGHT_BUDGET_PER_MINUTE", 1000
    ))

    # --- Admin ---
    admin_token: str = field(default_factory=lambda: _env_str("ADMIN_TOKEN", ""))

    excluded_symbols: frozenset = field(default_factory=lambda: frozenset({
        "USDTTRY", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
    }))

    def validate(self) -> None:
        problems = []

        if self.workers < 1:
            problems.append("MAX_WORKERS >= 1 olmalı")
        if self.shortlist_size < 1:
            problems.append("SHORTLIST_SIZE >= 1 olmalı")
        if self.scan_interval < 5:
            problems.append("SCAN_INTERVAL >= 5 olmalı")
        if self.max_signals < 1:
            problems.append("MAX_SIGNALS_PER_SCAN >= 1 olmalı")
        if self.request_timeout < 1:
            problems.append("REQUEST_TIMEOUT >= 1 olmalı")
        if not (0 < self.min_priority <= 100):
            problems.append("MIN_PRIORITY 0-100 arasında olmalı")
        if self.weight_budget_per_minute < 100:
            problems.append("WEIGHT_BUDGET_PER_MINUTE çok düşük")

        if not (0 < self.score_soft_cap <= 100):
            problems.append("SCORE_SOFT_CAP 0-100 arasında olmalı")
        if not (0 < self.priority_soft_cap <= 100):
            problems.append("PRIORITY_SOFT_CAP 0-100 arasında olmalı")
        if not (0 < self.entry_soft_cap <= 100):
            problems.append("ENTRY_SOFT_CAP 0-100 arasında olmalı")

        if self.min_adx_trend < 0 or self.early_adx_min < 0:
            problems.append("ADX eşikleri negatif olamaz")

        if not (0 < self.rsi_early_min < self.rsi_early_max < 100):
            problems.append("RSI_EARLY_MIN/MAX geçersiz")
        if not (self.rsi_early_max < self.rsi_overheated <= self.rsi_extreme <= 100):
            problems.append("RSI aşırı ısınma eşikleri geçersiz")

        if self.volume_accel_min_ratio <= 1:
            problems.append("VOLUME_ACCEL_MIN_RATIO > 1 olmalı")
        if self.volume_accel_strong_ratio < self.volume_accel_min_ratio:
            problems.append("VOLUME_ACCEL_STRONG_RATIO düşük")
        if self.volume_accel_early_ratio < self.volume_accel_strong_ratio:
            problems.append("VOLUME_ACCEL_EARLY_RATIO düşük")

        if self.trade_accel_min_previous < 1:
            problems.append("TRADE_ACCEL_MIN_PREVIOUS >= 1 olmalı")
        if self.trade_accel_ratio <= 1:
            problems.append("TRADE_ACCEL_RATIO > 1 olmalı")
        if self.trade_accel_strong_ratio < self.trade_accel_ratio:
            problems.append("TRADE_ACCEL_STRONG_RATIO düşük")

        if problems:
            raise ValueError(
                "Konfigürasyon hatası:\n- " + "\n- ".join(problems)
            )


SETTINGS = Settings()
