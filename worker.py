from __future__ import annotations

import logging
import os
import threading
import time

from scanner import MarketScanner


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("V29-WORKER")


scanner = MarketScanner(
    interval=os.getenv("SCAN_INTERVAL", "5m"),
    candle_limit=int(os.getenv("CANDLE_LIMIT", "100")),
)

INTERVAL_SECONDS = int(os.getenv("SCAN_SECONDS", "60"))


def scan_loop() -> None:
    logger.info("🐋 V29 worker başlatıldı")
    logger.info(
        "V29 ayarları | interval=%s | candle_limit=%s | scan_seconds=%s",
        scanner.interval,
        scanner.candle_limit,
        INTERVAL_SECONDS,
    )

    while True:
        started = time.time()

        try:
            logger.info("🔎 V29 tarama başladı")

            results = scanner.scan_all()

            buy_signals = [
                item
                for item in results
                if item.get("signal") == "BUY"
            ]

            watch_signals = [
                item
                for item in results
                if item.get("signal") == "WATCH"
            ]

            errors = [
                item
                for item in results
                if item.get("signal") == "ERROR"
            ]

            logger.info(
                "✅ V29 tarama tamamlandı | aday=%d | BUY=%d | WATCH=%d | ERROR=%d",
                len(results),
                len(buy_signals),
                len(watch_signals),
                len(errors),
            )

            for item in sorted(
                buy_signals,
                key=lambda x: float(x.get("score", 0)),
                reverse=True,
            )[:10]:
                logger.info(
                    "🟢 V29 BUY | %s | skor=%s | durum=%s | kıvrım=%s | erkenlik=%s",
                    item.get("symbol"),
                    item.get("score"),
                    item.get("status"),
                    item.get("curve_score"),
                    item.get("earlyness_score"),
                )

        except Exception:
            logger.exception("❌ V29 worker tarama hatası")

        elapsed = time.time() - started
        sleep_for = max(1, INTERVAL_SECONDS - int(elapsed))

        logger.info(
            "⏳ Sonraki V29 taraması yaklaşık %s saniye sonra",
            sleep_for,
        )

        time.sleep(sleep_for)


def start_worker() -> threading.Thread:
    thread = threading.Thread(
        target=scan_loop,
        name="v29-scanner",
        daemon=True,
    )

    thread.start()

    return thread
