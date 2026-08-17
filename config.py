from __future__ import annotations

import os
from dataclasses import dataclass


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} geçerli sayı değil"
        )


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} geçerli sayı değil"
        )


def env_str(name, default=""):
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:

    # ==========================================================
    # BINANCE TR
    # ==========================================================

    base_url: str = env_str(
        "BINANCE_TR_BASE",
        "https://api.binance.me",
    )

    scan_interval: int = env_int(
        "SCAN_INTERVAL",
        30,
    )

    workers: int = env_int(
        "MAX_WORKERS",
        10,
    )

    max_signals: int = env_int(
        "MAX_SIGNALS_PER_SCAN",
        3,
    )

    cooldown: int = env_int(
        "SIGNAL_COOLDOWN",
        900,
    )

    min_quote_volume: float = env_float(
        "MIN_QUOTE_VOLUME_TRY",
        1_000_000,
    )

    shortlist_size: int = env_int(
        "SHORTLIST_SIZE",
        80,
    )

    request_timeout: int = env_int(
        "REQUEST_TIMEOUT",
        8,
    )

    # ==========================================================
    # DATABASE
    # ==========================================================

    db_path: str = env_str(
        "STATE_DB_PATH",
        "balina_v26.db",
    )

    outcome_window: int = env_int(
        "OUTCOME_WINDOW",
        900,
    )

    signal_retention_days: int = env_int(
        "SIGNAL_RETENTION_DAYS",
        30,
    )

    # ==========================================================
    # TELEGRAM
    # ==========================================================

    telegram_token: str = env_str(
        "TELEGRAM_BOT_TOKEN",
    )

    telegram_chat: str = env_str(
        "TELEGRAM_CHAT_ID",
    )

    # ==========================================================
    # V26 - DARALMA
    # ==========================================================

    consolidation_bars: int = env_int(
        "CONSOLIDATION_BARS",
        30,
    )

    consolidation_max_range: float = env_float(
        "CONSOLIDATION_MAX_RANGE",
        3.5,
    )

    consolidation_max_bb_width: float = env_float(
        "CONSOLIDATION_MAX_BB_WIDTH",
        3.0,
    )

    # ==========================================================
    # HAREKETLI ORTALAMALAR
    # ==========================================================

    ma_fast: int = env_int(
        "MA_FAST",
        7,
    )

    ma_mid: int = env_int(
        "MA_MID",
        30,
    )

    ma_slow: int = env_int(
        "MA_SLOW",
        99,
    )

    ma7_break_pct: float = env_float(
        "MA7_BREAK_PCT",
        0.10,
    )

    # ==========================================================
    # HACIM
    # ==========================================================

    volume_ratio_buy: float = env_float(
        "VOLUME_RATIO_BUY",
        1.50,
    )

    volume_ratio_strong: float = env_float(
        "VOLUME_RATIO_STRONG",
        2.00,
    )

    buyer_pressure_min: float = env_float(
        "BUYER_PRESSURE_MIN",
        58,
    )

    buyer_pressure_strong: float = env_float(
        "BUYER_PRESSURE_STRONG",
        65,
    )

    # ==========================================================
    # MOMENTUM
    # ==========================================================

    rsi_min: float = env_float(
        "RSI_MIN",
        50,
    )

    rsi_max: float = env_float(
        "RSI_MAX",
        75,
    )

    adx_min: float = env_float(
        "ADX_MIN",
        20,
    )

    adx_strong: float = env_float(
        "ADX_STRONG",
        25,
    )

    momentum_min: float = env_float(
        "MOMENTUM_MIN",
        0.30,
    )

    # ==========================================================
    # KIRILIM
    # ==========================================================

    breakout_lookback: int = env_int(
        "BREAKOUT_LOOKBACK",
        30,
    )

    breakout_buffer: float = env_float(
        "BREAKOUT_BUFFER",
        0.10,
    )

    require_closed_breakout: bool = (
        env_str(
            "REQUIRE_CLOSED_BREAKOUT",
            "true",
        ).lower()
        == "true"
    )

    # ==========================================================
    # FAKEOUT
    # ==========================================================

    fakeout_max_wick: float = env_float(
        "FAKEOUT_MAX_WICK",
        45,
    )

    fakeout_min_close_position: float = env_float(
        "FAKEOUT_MIN_CLOSE_POSITION",
        60,
    )

    # ==========================================================
    # OPEN INTEREST
    # ==========================================================

    oi_enabled: bool = (
        env_str(
            "OI_ENABLED",
            "false",
        ).lower()
        == "true"
    )

    oi_min_change: float = env_float(
        "OI_MIN_CHANGE",
        1.0,
    )

    oi_strong_change: float = env_float(
        "OI_STRONG_CHANGE",
        2.0,
    )

    # ==========================================================
    # SKOR
    # ==========================================================

    min_score_buy: int = env_int(
        "MIN_SCORE_BUY",
        75,
    )

    min_score_strong: int = env_int(
        "MIN_SCORE_STRONG",
        85,
    )

    min_priority: int = env_int(
        "MIN_PRIORITY",
        65,
    )

    # ==========================================================
    # TEKRAR SİNYAL
    # ==========================================================

    repeat_window: int = env_int(
        "REPEAT_WINDOW",
        1800,
    )

    # ==========================================================
    # MARKET
    # ==========================================================

    market_symbol: str = env_str(
        "MARKET_SYMBOL",
        "BTCTRY",
    )

    market_move: float = env_float(
        "MARKET_MOVE",
        2.0,
    )

    # ==========================================================
    # RATE LIMIT
    # ==========================================================

    weight_budget_per_minute: int = env_int(
        "WEIGHT_BUDGET_PER_MINUTE",
        1000,
    )

    # ==========================================================
    # ADMIN
    # ==========================================================

    admin_token: str = env_str(
        "ADMIN_TOKEN",
    )

    # ==========================================================
    # HARIC TUTULANLAR
    # ==========================================================

    excluded_symbols: frozenset = frozenset({
        "USDTTRY",
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "BUSDUSDT",
        "DAIUSDT",
    })

    # ==========================================================
    # VALIDATION
    # ==========================================================

    def validate(self):

        if self.workers < 1:
            raise ValueError(
                "MAX_WORKERS >= 1 olmalı"
            )

        if self.scan_interval < 5:
            raise ValueError(
                "SCAN_INTERVAL >= 5 olmalı"
            )

        if self.shortlist_size < 1:
            raise ValueError(
                "SHORTLIST_SIZE >= 1 olmalı"
            )

        if self.request_timeout < 1:
            raise ValueError(
                "REQUEST_TIMEOUT >= 1 olmalı"
            )

        if self.ma_fast >= self.ma_mid:
            raise ValueError(
                "MA_FAST < MA_MID olmalı"
            )

        if self.ma_mid >= self.ma_slow:
            raise ValueError(
                "MA_MID < MA_SLOW olmalı"
            )

        if not (
            0
            < self.rsi_min
            < self.rsi_max
            <= 100
        ):
            raise ValueError(
                "RSI aralığı hatalı"
            )

        if not (
            0
            <= self.buyer_pressure_min
            <= 100
        ):
            raise ValueError(
                "BUYER_PRESSURE_MIN hatalı"
            )

        if (
            self.min_score_buy
            > self.min_score_strong
        ):
            raise ValueError(
                "MIN_SCORE_BUY <= "
                "MIN_SCORE_STRONG olmalı"
            )

        if self.weight_budget_per_minute < 100:
            raise ValueError(
                "WEIGHT_BUDGET_PER_MINUTE "
                "çok düşük"
            )


SETTINGS = Settings()
