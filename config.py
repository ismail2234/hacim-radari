"""
Merkezi konfigürasyon.

Eski versiyonda 40+ env var modül seviyesinde, gevşek tiplerle okunuyordu.
Yanlış/eksik bir değer (örn. MAX_WORKERS="abc") import anında anlaşılmaz bir
ValueError ile patlıyordu ve hangi ayarın bozuk olduğunu anlamak zordu.

Burada tek bir dataclass'ta topluyoruz, hepsini tip dönüşümüyle okuyoruz ve
mantıksal tutarlılığı (örn. TOP_PRIORITY <= SHORTLIST) doğruluyoruz.
"""

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
    base_url: str = field(default_factory=lambda: _env_str(
        "BINANCE_TR_BASE", "https://api.binance.me"))
    scan_interval: int = field(default_factory=lambda: _env_int("SCAN_INTERVAL", 30))
    workers: int = field(default_factory=lambda: _env_int("MAX_WORKERS", 10))
    max_signals: int = field(default_factory=lambda: _env_int("MAX_SIGNALS_PER_SCAN", 3))
    cooldown: int = field(default_factory=lambda: _env_int("SIGNAL_COOLDOWN", 1200))
    min_quote_volume: float = field(default_factory=lambda: _env_float("MIN_QUOTE_VOLUME_TRY", 1_000_000))
    shortlist_size: int = field(default_factory=lambda: _env_int("SHORTLIST_SIZE", 80))
    request_timeout: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT", 8))
    db_path: str = field(default_factory=lambda: _env_str("STATE_DB_PATH", "balina_v23.db"))
    outcome_window: int = field(default_factory=lambda: _env_int("OUTCOME_WINDOW", 900))
    signal_retention_days: int = field(default_factory=lambda: _env_int("SIGNAL_RETENTION_DAYS", 30))

    telegram_token: str = field(default_factory=lambda: _env_str("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat: str = field(default_factory=lambda: _env_str("TELEGRAM_CHAT_ID", ""))

    lt30_mild: float = field(default_factory=lambda: _env_float("LT30_MILD", -20))
    lt30_strong: float = field(default_factory=lambda: _env_float("LT30_STRONG", -35))
    lt90_mild: float = field(default_factory=lambda: _env_float("LT90_MILD", -30))
    lt90_strong: float = field(default_factory=lambda: _env_float("LT90_STRONG", -50))
    lt90_extreme: float = field(default_factory=lambda: _env_float("LT90_EXTREME", -65))

    daily_cache_ttl: int = field(default_factory=lambda: _env_int("DAILY_CACHE_TTL", 900))

    min_1m_trades: int = field(default_factory=lambda: _env_int("MIN_1M_TRADES", 20))
    min_5m_trades: int = field(default_factory=lambda: _env_int("MIN_5M_TRADES", 50))
    trade_reference: int = field(default_factory=lambda: _env_int("TRADE_REFERENCE", 100))

    streak_window: int = field(default_factory=lambda: _env_int("STREAK_WINDOW", 180))
    buy_streak: int = field(default_factory=lambda: _env_int("BUY_STREAK", 2))
    very_streak: int = field(default_factory=lambda: _env_int("VERY_STREAK", 2))

    market_symbol: str = field(default_factory=lambda: _env_str("MARKET_SYMBOL", "BTCTRY"))
    market_move: float = field(default_factory=lambda: _env_float("MARKET_MOVE", 2))

    top_priority: int = field(default_factory=lambda: _env_int("TOP_PRIORITY", 5))
    min_priority: int = field(default_factory=lambda: _env_int("MIN_PRIORITY", 60))

    trap_buyer: float = field(default_factory=lambda: _env_float("TRAP_BUYER", 50))
    trap_volume: float = field(default_factory=lambda: _env_float("TRAP_VOLUME", 1.8))
    trap_momentum: float = field(default_factory=lambda: _env_float("TRAP_MOMENTUM", -1.2))

    # Binance ağırlık bütçesi: dakikalık istek ağırlığı limiti (bkz. rate_limiter.py)
    weight_budget_per_minute: int = field(default_factory=lambda: _env_int("WEIGHT_BUDGET_PER_MINUTE", 1000))

    # /performance gibi iç endpoint'leri korumak için basit paylaşılan anahtar.
    # Boş bırakılırsa (varsayılan DEĞİL, bilinçli olarak) endpoint korumasız kalır
    # ve bunu loop() başlangıcında
