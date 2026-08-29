from __future__ import annotations

import threading
import time

from config import SCAN_SECONDS, MIN_BUY_SCORE
from scanner import V30Scanner
from telegram_notifier import TelegramNotifier


scanner = V30Scanner()
telegram = TelegramNotifier()

LAST_SENT: dict[str, float] = {}
COOLDOWN_SECONDS = 15 * 60


def log(message: str) -> None:
    print(f"[V30 WORKER] {message}", flush=True)


def scan_once() -> None:
    try:
        log("Piyasa taraması başlıyor...")

        candidates = scanner.buy_candidates()

        log(f"BUY adayı: {len(candidates)}")

        now = time.time()

        for result in candidates:
            symbol = str(result.get("symbol", ""))

            if not symbol:
                continue

            score = float(result.get("score", 0))

            if score < MIN_BUY_SCORE:
                continue

            last_sent = LAST_SENT.get(symbol, 0)

            if now - last_sent < COOLDOWN_SECONDS:
                continue

            if not telegram.enabled:
                log("Telegram Variables bulunamadı.")
                continue

            if telegram.send_signal(result):
                LAST_SENT[symbol] = now
                log(
                    f"TELEGRAM GÖNDERİLDİ: "
                    f"{symbol} | skor={score:.1f}"
                )
            else:
                log(
                    f"Telegram gönderilemedi: {symbol}"
                )

    except Exception as exc:
        log(f"TARAMA HATASI: {exc}")


def worker_loop() -> None:
    log("V30 Worker başlatıldı.")
    log(f"Tarama aralığı: {SCAN_SECONDS} saniye")
    log(
        "Telegram: "
        + ("AKTİF" if telegram.enabled else "PASİF")
    )

    while True:
        scan_once()

        log(
            f"Sonraki tarama "
            f"{SCAN_SECONDS} saniye sonra."
        )

        time.sleep(SCAN_SECONDS)


def start_worker() -> None:
    thread = threading.Thread(
        target=worker_loop,
        daemon=True,
    )

    thread.start()

    log("Scanner thread çalışıyor.")
