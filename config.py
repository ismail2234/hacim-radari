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

    # --- YENİ: skor/priority/entry'nin fazla kolay 90+ çıkmasını önlemek
    # için "yumuşak tavan" (soft cap). Bu eşiğin altı normal, üstü
    # sıkıştırılarak (faktörle çarpılarak) büyür -- yani 90+ görmek için
    # ham puanın çok daha yüksek olması gerekir.
    score_soft_cap: int = field(default_factory=lambda: _env_int("SCORE_SOFT_CAP", 80))
    score_soft_cap_factor: float = field(default_factory=lambda: _env_float("SCORE_SOFT_CAP_FACTOR", 0.40))

    priority_soft_cap: int = field(default_factory=lambda: _env_int("PRIORITY_SOFT_CAP", 75))
    priority_soft_cap_factor: float = field(default_factory=lambda: _env_float("PRIORITY_SOFT_CAP_FACTOR", 0.40))

    entry_soft_cap: int = field(default_factory=lambda: _env_int("ENTRY_SOFT_CAP", 78))
    entry_soft_cap_factor: float = field(default_factory=lambda: _env_float("ENTRY_SOFT_CAP_FACTOR", 0.35))

    # --- YENİ: ADX 18 gerçek bir "güçlü trend" için yetersiz kabul edilir
    # (yaygın teknik analiz kuralı: ADX>25 güçlü trend, <20 zayıf/yatay).
    # Eski kod 18'i "güçlü" sayıyordu -- bu eşik artık ayrı bir ayardır.
    min_adx_trend: int = field(default_factory=lambda: _env_int("MIN_ADX_TREND", 25))
    weak_adx_penalty: int = field(default_factory=lambda: _env_int("WEAK_ADX_PENALTY", 5))

    market_symbol: str = field(default_factory=lambda: _env_str("MARKET_SYMBOL", "BTCTRY"))
    market_move: float = field(default_factory=lambda: _env_float("MARKET_MOVE", 2))

    top_priority: int = field(default_factory=lambda: _env_int("TOP_PRIORITY", 5))
    min_priority: int = field(default_factory=lambda: _env_int("MIN_PRIORITY", 60))

    # --- YENİ (Faz B / ÖNCÜ AL sistemi): üç aşamalı bağımsız kriter yapısı.
    # decide_stage() bu eşikleri, 7 bağımsız kriterden kaçının sağlandığına
    # bakarak kullanır -- eski skor tabanlı (setup>=25, score>=68 vb.)
    # eşiklerin yerini alır.
    oncu_min_criteria: int = field(default_factory=lambda: _env_int("ONCU_MIN_CRITERIA", 3))
    buy_min_criteria: int = field(default_factory=lambda: _env_int("BUY_MIN_CRITERIA", 5))
    very_min_criteria: int = field(default_factory=lambda: _env_int("VERY_MIN_CRITERIA", 6))

    # ÖNCÜ AL sadece RSI henüz aşırı ısınmamışken tetiklenir -- RSI zaten
    # yüksekse "hareketin başlangıcı" değil, zaten olgunlaşmış bir harekettir.
    oncu_rsi_max: float = field(default_factory=lambda: _env_float("ONCU_RSI_MAX", 80))

    # "RSI sağlıklı bantta ve yükseliyor" kriteri için bant aralığı.
    healthy_rsi_low: float = field(default_factory=lambda: _env_float("HEALTHY_RSI_LOW", 50))
    healthy_rsi_high: float = field(default_factory=lambda: _env_float("HEALTHY_RSI_HIGH", 75))

    # AL seviyesi, kriter sayısının yanında ayrıca "momentum teyidi" ister
    # (ÖNCÜ AL'dan ayırt etmek için) -- bu eşikler o teyidi tanımlar.
    al_momentum_confirm: float = field(default_factory=lambda: _env_float("AL_MOMENTUM_CONFIRM", 0.3))
    al_volume_confirm: float = field(default_factory=lambda: _env_float("AL_VOLUME_CONFIRM", 1.5))

    # ÖNCÜ AL için ayrı ve daha düşük bir MIN_PRIORITY eşiği -- erken
    # sinyaller doğası gereği daha az teyide sahip olduğundan priority'leri
    # BUY/VERY kadar yüksek olmaz. Aynı eşiği kullanmak erken sinyalleri
    # boğar (bkz. kullanıcı geri bildirimi madde 11).
    min_priority_oncu: int = field(default_factory=lambda: _env_int("MIN_PRIORITY_ONCU", 35))

    # YENİ (V25): birikim/akümülasyon (WATCH) tier ayarları.
    watch_min_criteria: int = field(default_factory=lambda: _env_int("WATCH_MIN_CRITERIA", 3))
    min_priority_watch: int = field(default_factory=lambda: _env_int("MIN_PRIORITY_WATCH", 40))

    # YENİ (tarama sağlığı izleme): hata oranı bu eşiği geçerse bot kendi
    # kendine Telegram'a uyarı gönderir -- loglara manuel bakmayı beklemek
    # yerine sorunu proaktif olarak bildirir. Spam olmasın diye soğuma
    # süresi var.
    health_alert_error_rate: float = field(default_factory=lambda: _env_float("HEALTH_ALERT_ERROR_RATE", 0.30))
    health_alert_cooldown: int = field(default_factory=lambda: _env_int("HEALTH_ALERT_COOLDOWN", 1800))

    trap_buyer: float = field(default_factory=lambda: _env_float("TRAP_BUYER", 50))
    trap_volume: float = field(default_factory=lambda: _env_float("TRAP_VOLUME", 1.8))
    trap_momentum: float = field(default_factory=lambda: _env_float("TRAP_MOMENTUM", -1.2))

    # Binance ağırlık bütçesi: dakikalık istek ağırlığı limiti (bkz. rate_limiter.py)
    weight_budget_per_minute: int = field(default_factory=lambda: _env_int("WEIGHT_BUDGET_PER_MINUTE", 1000))

    # /performance gibi iç endpoint'leri korumak için basit paylaşılan anahtar.
    # Boş bırakılırsa (varsayılan DEĞİL, bilinçli olarak) endpoint korumasız kalır
    # ve bunu loop() başlangıcında uyarı olarak loglarız.
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
            problems.append("SCAN_INTERVAL çok düşük (>=5 önerilir)")
        if self.max_signals < 1:
            problems.append("MAX_SIGNALS_PER_SCAN >= 1 olmalı")
        if self.request_timeout < 1:
            problems.append("REQUEST_TIMEOUT >= 1 olmalı")
        if not (0 < self.min_priority <= 100):
            problems.append("MIN_PRIORITY 0-100 arasında olmalı")
        if self.weight_budget_per_minute < 100:
            problems.append("WEIGHT_BUDGET_PER_MINUTE çok düşük, tarama hiç ilerlemez")
        if not (0 < self.score_soft_cap <= 100):
            problems.append("SCORE_SOFT_CAP 0-100 arasında olmalı")
        if not (0 < self.priority_soft_cap <= 100):
            problems.append("PRIORITY_SOFT_CAP 0-100 arasında olmalı")
        if not (0 < self.entry_soft_cap <= 100):
            problems.append("ENTRY_SOFT_CAP 0-100 arasında olmalı")
        if self.min_adx_trend < 0:
            problems.append("MIN_ADX_TREND negatif olamaz")
        if not (0 < self.oncu_min_criteria <= self.buy_min_criteria <= self.very_min_criteria <= 7):
            problems.append("ONCU_MIN_CRITERIA <= BUY_MIN_CRITERIA <= VERY_MIN_CRITERIA <= 7 olmalı")
        if not (0 < self.min_priority_oncu <= self.min_priority):
            problems.append("MIN_PRIORITY_ONCU, MIN_PRIORITY'den büyük olmamalı")
        if not (0 < self.health_alert_error_rate <= 1):
            problems.append("HEALTH_ALERT_ERROR_RATE 0-1 arasında olmalı")
        if not (0 < self.watch_min_criteria <= 5):
            problems.append("WATCH_MIN_CRITERIA 1-5 arasında olmalı")

        if problems:
            raise ValueError(
                "Konfigürasyon hatası:\n- " + "\n- ".join(problems)
            )


SETTINGS = Settings()
