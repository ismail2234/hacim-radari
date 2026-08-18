"""
V26 - Ayarlar
Butun modullerin ortak kullandigi konfigurasyon sozlugu burada.

Bu surum Telegram'a DOGRUDAN mesaj atar (bot_token + chat_id ile).
Sinyaller ayrica signals.jsonl dosyasina da yazilmaya devam eder (yedek/log amacli).

Railway'de calisirken hassas bilgiler ve tarama listesi kod icine
yazilmaz; Railway proje panelinden "Variables" sekmesinde environment
variable olarak girilir:

  TELEGRAM_BOT_TOKEN = 123456:ABC-xxxxx
  TELEGRAM_CHAT_ID   = 123456789
  SCAN_SYMBOLS       = PORTAL/TRY,BTC/TRY,ETH/TRY   (virgulle ayrilmis)
  SCAN_INTERVAL_SEC  = 300
  STARTING_CAPITAL   = 10000
  SIGNALS_FILE       = signals.jsonl   (istege bagli, farkli yol/isim icin)
"""

import os

CONFIG = {
    "exchange": "binance",
    "quote": "TRY",
    "timeframe": "1h",
    "lookback_candles": 300,

    "vol_avg_period": 20,
    "vol_spike_ratio": 2.0,

    "ma_periods": (7, 30, 99),
    "rsi_period": 14,
    "atr_period": 14,
    "bb_period": 20,

    "score_weights": {
        "hacim": 25,
        "fiyat_yapisi": 20,
        "momentum": 30,
        "ma_hizalanma": 15,
        "volatilite": 10,
    },

    "score_thresholds": {
        "strong": 80,
        "watch": 70,
        "prep": 60,
    },

    "min_24h_volume_try": 50_000_000,
    "btc_symbol": "BTC/USDT",

    "risk": {
        "atr_stop_mult": 1.5,
        "recent_low_window": 10,
        "ma_stop_buffer": 0.98,
        "risk_pct_per_trade": 0.01,  # sermayenin %1'i
    },

    "telegram": {
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN"),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
    },

    # Railway'de bu degerler Variables sekmesinden okunur.
    # Yerelde test ederken bos birakilirsa varsayilanlar kullanilir.
    "scan_symbols": [
        s.strip() for s in os.environ.get("SCAN_SYMBOLS", "PORTAL/TRY,BTC/TRY").split(",") if s.strip()
    ],
    "scan_interval_sec": int(os.environ.get("SCAN_INTERVAL_SEC", "300")),
    "starting_capital": float(os.environ.get("STARTING_CAPITAL", "10000")),
}
