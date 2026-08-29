from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from app import app
from config import SCAN_SECONDS
from scanner import V30Scanner
from telegram_notifier import TelegramNotifier


scanner = V30Scanner()
telegram = TelegramNotifier()

# Aynı coinin kısa sürede tekrar tekrar mesaj göndermesini engeller.
LAST_SENT = {}
COOLDOWN_SECONDS = 15 * 60


def log(message: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[V30 WORKER] {now} | {message}", flush=True)


def send_candidates() -> None:
    try:
        log("Piyasa taraması başlıyor...")

        candidates = scanner.buy_candidates()

        log(f"BUY adayı: {len(candidates)}")

        if not candidates:
            return

        now = time.time()

        for result in candidates:
            symbol = str(result.get("symbol", ""))

            if not symbol:
                continue

            last_time = LAST_SENT.get(symbol, 0)

            if now - last_time < COOLDOWN_SECONDS:
                log(f"{symbol} cooldown nedeniyle atlandı.")
                continue

            score = float(result.get("score", 0))

            # Güvenlik: sadece gerçek BUY ve minimum skor
            if result.get("signal") != "BUY":
                continue

            if score < 75:
                continue

            success = telegram.send_signal(result)

            if success:
                LAST_SENT[symbol] = now
                log(
                    f"TELEGRAM GÖNDERİLDİ: "
                    f"{symbol} | skor={score:.1f}"
                )
            else:
                log(
                    f"Telegram gönderilemedi: "
                    f"{symbol}"
                )

    except Exception as exc:
        log(f"TARAMA HATASI: {exc}")


def scanner_loop() -> None:
    log("V30 Worker başlatıldı.")
    log(f"Tarama aralığı: {SCAN_SECONDS} saniye")
    log(
        "Telegram: "
        + ("AKTİF" if telegram.enabled else "PASİF")
    )

    while True:
        send_candidates()

        log(
            f"Sonraki tarama {SCAN_SECONDS} saniye sonra."
        )

        time.sleep(SCAN_SECONDS)


def start_worker() -> None:
    thread = threading.Thread(
        target=scanner_loop,
        daemon=True,
    )

    thread.start()

    log("Scanner thread çalışıyor.")


# Railway web servisi için Flask sunucusunu ayrıca çalıştır.
def start_flask() -> None:
    port = int(os.getenv("PORT", "8080"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    start_worker()
    start_flask()
