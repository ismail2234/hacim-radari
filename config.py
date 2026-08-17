import os
from dataclasses import dataclass

def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        raise ValueError(f"{name} sayi olmali")

def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        raise ValueError(f"{name} sayi olmali")

@dataclass(frozen=True)
class Settings:
    # Binance
    base_url: str = os.getenv(
        "BINANCE_TR_BASE",
        "https://api.binance.me"
    )
    timeout: int = _int("REQUEST_TIMEOUT", 10)

    # Tarama
    scan_interval: int = _int("SCAN_INTERVAL", 30)
    workers: int = _int("MAX_WORKERS", 8)
    shortlist_size: int = _int("SHORTLIST_SIZE", 80)
    max_signals: int = _int("MAX_SIGNALS_PER_SCAN", 3)

    # TRY hacim
    min_quote_volume: float = _float(
        "MIN_QUOTE_VOLUME_TRY", 500000
    )

    # V26 skorları
    radar_score: int = _int("V26_RADAR_SCORE", 70)
    develop_score: int = _int("V26_DEVELOP_SCORE", 80)
    buy_score: int = _int("V26_BUY_SCORE", 88)

    # Hacim
    volume_confirm: float = _float(
        "V26_VOLUME_CONFIRM", 2.0
    )

    # Mum
    min_body: float = _float(
        "V26_MIN_BODY", 0.45
    )

    # Telegram
    telegram_token: str = os.getenv(
        "TELEGRAM_BOT_TOKEN", ""
    )
    telegram_chat: str = os.getenv(
        "TELEGRAM_CHAT_ID", ""
    )

    # Veritabani
    db_path: str = os.getenv(
        "STATE_DB_PATH", "v26.db"
    )

    # Market
    market_symbol: str = os.getenv(
        "MARKET_SYMBOL", "BTCTRY"
    )

    # Sinyal tekrar kontrolu
    signal_cooldown: int = _int(
        "SIGNAL_COOLDOWN", 300
    )

    # Hariç tutulacaklar
    excluded_symbols: tuple = (
        "USDTTRY",
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "BUSDUSDT",
        "DAIUSDT",
    )

    def validate(self):
        if not self.telegram_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN eksik"
            )

        if not self.telegram_chat:
            raise ValueError(
                "TELEGRAM_CHAT_ID eksik"
            )

        if self.scan_interval < 5:
            raise ValueError(
                "SCAN_INTERVAL en az 5 olmali"
            )

        if not 0 < self.radar_score <= 100:
            raise ValueError(
                "V26_RADAR_SCORE 0-100 olmali"
            )

        if not (
            self.radar_score
            < self.develop_score
            < self.buy_score
            <= 100
        ):
            raise ValueError(
                "V26 skor siralamasi hatali"
            )

        if self.min_quote_volume <= 0:
            raise ValueError(
                "MIN_QUOTE_VOLUME_TRY pozitif olmali"
            )

        if self.volume_confirm < 1:
            raise ValueError(
                "V26_VOLUME_CONFIRM en az 1 olmali"
            )


SETTINGS = Settings()
SETTINGS.validate()
