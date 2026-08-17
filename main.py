from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread

from flask import Flask, abort, request
from binance_client import BinanceClient
from config import SETTINGS, Settings
from db import DB
from indicators import avg
from market import MarketData
from rate_limiter import RateLimiter
from scoring import analyze, rank_signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("balina.main")

SETTINGS.validate()

LIMITER = RateLimiter(SETTINGS.weight_budget_per_minute)
CLIENT = BinanceClient(SETTINGS, LIMITER)
DBS = DB(SETTINGS.db_path, SETTINGS.signal_retention_days)
MARKET = MarketData(CLIENT, SETTINGS)


def candidates(cfg: Settings, data: list) -> list[dict]:
    result = []

    for x in data:
        symbol = x.get("symbol", "")

        if not symbol.endswith("TRY") or symbol in cfg.excluded_symbols:
            continue

        try:
            volume = float(x.get("quoteVolume", 0))
            change = float(x.get("priceChangePercent", 0))
            price = float(x.get("lastPrice", 0))

            if volume < cfg.min_quote_volume or change > 25 or price <= 0:
                continue

            result.append({
                "symbol": symbol,
                "volume": volume,
                "chg": change,
                "price": price,
            })
        except (TypeError, ValueError):
            continue

    return result


def shortlist(cfg: Settings, items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda x: x["volume"] * (1 + max(x["chg"], 0) / 100),
        reverse=True,
    )[:cfg.shortlist_size]


def message(r: dict) -> str:
    title = {
        "WATCH": "🔵 İZLE",
        "ONCU": "🟡 ÖNCÜ AL",
        "BUY": "🟢 AL",
        "VERY": "🚀 GÜÇLÜ AL",
    }.get(r["status"], r["status"])

    criteria = ", ".join(r.get("criteria_list", [])) or "Teyit yok"

    d30 = f"{r['d30']:+.1f}%" if r.get("d30") is not None else "VERİ YOK"
    d90 = f"{r['d90']:+.1f}%" if r.get("d90") is not None else "VERİ YOK"

    if r["status"] == "WATCH":
        return (
            "🐋 BALİNA RADARI V25\n\n"
            f"{title}\n\n"
            f"🪙 #{r['symbol']}\n"
            f"💰 {r['price']:.8g}\n"
            f"📅 30g: {d30} | 90g: {d90}\n\n"
            f"🔵 Birikim: {r['accumulation_count']}/5\n"
            f"✅ {', '.    elapsed = time.time() - started

    log.info(
        "V25 | TRY:%d/%d | İZLE:%d | ÖNCÜ:%d | AL:%d | VERY:%d | "
        "Hata:%d | Gönder:%d | %.1fs",
        len(items),
        len(all_candidates),
        stats.get("WATCH", 0),
        stats.get("ONCU", 0),
        stats.get("BUY", 0),
        stats.get("VERY", 0),
        stats.get("error", 0),
        sent,
        elapsed,
    )

    return (
        stats.get("error", 0) / max(1, len(items)) > 0.30
        or elapsed > SETTINGS.scan_interval * 1.25
    )


def loop():
    log.info("🐋 BALİNA RADARI V25 başlatılıyor...")

    try:
        info = CLIENT.exchange_info()
        symbols = {
            x.get("symbol")
            for x in info.get("symbols", [])
        }

        try_count = sum(
            s.endswith("TRY")
            for s in symbols
            if s
        )

        if try_count <= 0:
            raise RuntimeError("TRY market bulunamadı.")

        log.info("Binance doğrulandı | TRY:%d", try_count)

    except Exception as e:
        log.exception("MARKET DOĞRULAMA HATASI: %s", e)
        return

    if SETTINGS.telegram_token and SETTINGS.telegram_chat:
        CLIENT.telegram(
            "🐋 BALİNA RADARI V25 AKTİF\n"
            "🔵 Birikim tespiti aktif\n"
            "🟡 Öncü AL aktif\n"
            "🟢 Çok aşamalı karar motoru aktif\n"
            "📊 Hacim + işlem + alıcı + EMA + MACD + RSI\n"
            "🛡️ Rate-limit koruması aktif"
        )

    last_cleanup = 0.0

    while True:
        started = time.time()

        try:
            backoff = scan()
        except Exception:
            log.exception("Tarama döngüsü hatası")
            backoff = True

        if started - last_cleanup > 86400:
            try:
                removed = DBS.cleanup_old_signals()
                if removed:
                    log.info(
                        "Retention: %d eski sinyal silindi.",
                        removed,
                    )
            except Exception:
                log.exception("DB temizliği başarısız")

            last_cleanup = started

        elapsed = time.time() - started

        if backoff:
            time.sleep(
                max(
                    180,
                    SETTINGS.scan_interval * 3,
                )
            )
        else:
            time.sleep(
                max(
                    1,
                    SETTINGS.scan_interval - elapsed,
                )
            )


app = Flask(__name__)


@app.route("/")
def home():
    return "🐋 Balina Radarı V25 Aktif"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V25",
        "scan_interval": SETTINGS.scan_interval,
        "workers": SETTINGS.workers,
        "rate_limit": LIMITER.snapshot(),
    }


@app.route("/performance")
def performance():
    return {
        "samples": len(DBS.performance_summary())
    }


Thread(
    target=loop,
    daemon=True,
    name="balina-v25",
).start()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        use_reloader=False,
    )
