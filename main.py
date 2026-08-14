from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
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

LIMITER = RateLimiter(
    SETTINGS.weight_budget_per_minute
)

CLIENT = BinanceClient(
    SETTINGS,
    LIMITER,
)

DBS = DB(
    SETTINGS.db_path,
    retention_days=SETTINGS.signal_retention_days,
)

MARKET = MarketData(
    CLIENT,
    SETTINGS,
)


def candidates(
    cfg: Settings,
    data: list,
) -> list[dict]:
    result = []

    for ticker in data:
        if not isinstance(ticker, dict):
            continue

        symbol = ticker.get("symbol", "")

        if (
            not symbol.endswith("TRY")
            or symbol in cfg.excluded_symbols
        ):
            continue

        try:
            volume = float(
                ticker.get("quoteVolume", 0)
            )
            change = float(
                ticker.get("priceChangePercent", 0)
            )
            price = float(
                ticker.get("lastPrice", 0)
            )
        except (TypeError, ValueError):
            continue

        if volume < cfg.min_quote_volume:
            continue

        if change > 25:
            continue

        if price <= 0:
            continue

        result.append(
            {
                "symbol": symbol,
                "volume": volume,
                "chg": change,
                "price": price,
            }
        )

    return result


def shortlist(
    cfg: Settings,
    items: list[dict],
) -> list[dict]:
    def rank(item: dict) -> float:
        change = max(
            float(item.get("chg", 0)),
            0.0,
        )

        return float(
            item.get("volume", 0)
        ) * (1 + change / 100)

    return sorted(
        items,
        key=rank,
        reverse=True,
    )[:cfg.shortlist_size]


def message(r: dict) -> str:
    status = r.get("status", "BUY")

    if status == "VERY":
        title = "🔥 ÇOK GÜÇLÜ AL"
    else:
        title = "🟢 AL"

    reasons: list[str] = []

    if r.get("closed_breakout"):
        reasons.append("Kapanış kırılımı")
    elif r.get("breakout"):
        reasons.append("Direnç kırıldı")
    elif r.get("dist", 999) <= 0.35:
        reasons.append(
            f"Direnç %{r.get('dist', 0):.2f}"
        )

    if r.get("vr", 0) >= 1.5:
        reasons.append(
            f"1m hacim {r.get('vr', 0):.1f}x"
        )

    if r.get("vr5", 0) >= 1.5:
        reasons.append(
            f"5m hacim {r.get('vr5', 0):.1f}x"
        )

    if r.get("impulse", 0) >= 2:
        reasons.append(
            f"İvme {r.get('impulse', 0):.1f}x"
        )

    if r.get("bp", 0) >= 65:
        reasons.append(
            f"Alıcı %{r.get('bp', 0):.0f}"
        )

    if r.get("ema"):
        reasons.append("EMA trend")

    if r.get("macd"):
        reasons.append("MACD güçleniyor")

    if r.get("hl"):
        reasons.append("Higher-Low")

    if r.get("squeeze"):
        reasons.append("BB sıkışma")

    if (
        r.get("trades_1m", 0)
        >= SETTINGS.min_1m_trades
    ):
        reasons.append(
            "İşlem katılımı güçlü"
        )

    trap_text = ""

    if r.get("trap"):
        trap_reasons = r.get(
            "trap_reasons",
            [],
        )

        if trap_reasons:
            trap_text = (
                "\n⚠️ TUZAK: "
                + ", ".join(trap_reasons)
                + "\n"
            )

    if status == "VERY":
        result = "🚀 Güçlü teyit."
    elif r.get("closed_breakout"):
        result = "🎯 Alım teyidi oluştu."
    else:
        result = "🟡 Kırılım teyidi bekleniyor."

    d30 = r.get("d30")
    d90 = r.get("d90")

    d30_text = (
        f"{d30:+.1f}%"
        if d30 is not None
        else "VERİ YOK"
    )

    d90_text = (
        f"{d90:+.1f}%"
        if d90 is not None
        else "VERİ YOK"
    )

    return (
        "🐋 BALİNA RADARI V23\n\n"
        f"{title}\n\n"
        f"🪙 #{r.get('symbol', '')}\n"
        f"💰 {r.get('price', 0):.8g}\n"
        f"💪 Güç: {r.get('score', 0)}/100\n"
        f"🏆 Öncelik: {r.get('priority', 0):.0f}/100\n"
        f"🎯 Giriş: {r.get('entry_quality', 0)}/100\n"
        f"🔁 Teyit: {r.get('streak', 0)}x\n\n"
        f"📊 1m Hacim: {r.get('vr', 0):.2f}x"
        f" | 5m: {r.get('vr5', 0):.2f}x\n"
        f"🚀 İvme: {r.get('impulse', 0):.2f}x\n"
        f"🛒 Alıcı: %{r.get('bp', 0):.0f}\n"
        f"🔢 İşlem: {r.get('trades_1m', 0)}\n"
        f"📈 RSI: {r.get('rv', 0):.0f}"
        f" | ADX: {r.get('ad', 0):.0f}\n"
        f"🎯 Direnç: %{r.get('dist', 0):.2f}\n"
        f"🚀 Kırılım: "
        f"{'✅' if r.get('breakout') else '⏳'}\n"
        f"📅 30g: {d30_text}"
        f" | 90g: {d90_text}\n"
        f"🌐 BTC/TRY: "
        f"{r.get('market_momentum', 0):+.2f}%\n"
        f"{trap_text}\n"
        f"🔎 {' • '.join(reasons[:8])}\n\n"
        f"{result}"
    )


def _price_map(
    data: list,
) -> dict[str, float]:
    prices: dict[str, float] = {}

    for item in data:
        if not isinstance(item, dict):
            continue

        symbol = item.get("symbol")

        if not symbol:
            continue

        try:
            price = float(
                item.get("lastPrice", 0)
            )
        except (TypeError, ValueError):
            continue

        if price > 0:
            prices[symbol] = price

    return prices


def _analyze_one(
    item: dict,
) -> dict:
    try:
        return analyze(
            SETTINGS,
            CLIENT,
            DBS,
            MARKET,
            item,
        )
    except Exception:
        symbol = item.get(
            "symbol",
            "UNKNOWN",
        )

        log.exception(
            "%s analiz worker hatası",
            symbol,
        )

        return {
            "status": "error",
            "symbol": symbol,
        }


def scan() -> bool:
    started = time.monotonic()

    data = CLIENT.tickers()

    if not data:
        log.warning(
            "Ticker verisi alınamadı."
        )
        return True

    prices = _price_map(data)

    try:
        DBS.update_outcomes(
            prices,
            SETTINGS.outcome_window,
        )
    except Exception:
        log.exception(
            "Outcome güncellemesi başarısız"
        )

    all_candidates = candidates(
        SETTINGS,
        data,
    )

    items = shortlist(
        SETTINGS,
        all_candidates,
    )

    if not items:
        log.info(
            "V23 | Aday bulunamadı | %.1fs",
            time.monotonic() - started,
        )
        return False

    signals: list[dict] = []
    stats: dict[str, int] = {}

    with ThreadPoolExecutor(
        max_workers=SETTINGS.workers,
        thread_name_prefix="scanner",
    ) as executor:

        futures = [
            executor.submit(
                _analyze_one,
                item,
            )
            for item in items
        ]

        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                result = {
                    "status": "error"
                }

            status = result.get(
                "status",
                "error",
            )

            stats[status] = (
                stats.get(status, 0) + 1
            )

            if status in {
                "BUY",
                "VERY",
            }:
                signals.append(result)

    signals = rank_signals(
        SETTINGS,
        signals,
    )

    sent = 0

    for signal in signals:
        if sent >= SETTINGS.max_signals:
            break

        priority = float(
            signal.get("priority", 0)
        )

        if priority < SETTINGS.min_priority:
            continue

        symbol = signal.get(
            "symbol",
            "",
        )

        status = signal.get(
            "status",
            "BUY",
        )

        if not DBS.can_send(
            symbol,
            status,
            SETTINGS.cooldown,
        ):
            continue

        text = message(signal)

        if not CLIENT.telegram(text):
            log.warning(
                "%s Telegram gönderilemedi",
                symbol,
            )
            continue

        try:
            DBS.put(
                symbol,
                signal.get("score", 0),
                status,
                status,
                sent=time.time(),
                streak=signal.get(
                    "streak",
                    0,
                ),
                trap=signal.get(
                    "trap",
                    False,
                ),
                priority=priority,
            )

            DBS.create_signal(signal)

            sent += 1

        except Exception:
            log.exception(
                "%s sinyal DB kaydı başarısız",
                symbol,
            )

        time.sleep(0.3)

    elapsed = (
        time.monotonic()
        - started
    )

    errors = stats.get(
        "error",
        0,
    )

    error_ratio = (
        errors / max(1, len(items))
    )

    log.info(
        "V23 | TRY:%d/%d | AL:%d | "
        "VERY:%d | INTERNAL:%d | "
        "PASS:%d | Hata:%d | Gönder:%d | "
        "%.1fs | budget:%s",
        len(items),
        len(all_candidates),
        stats.get("BUY", 0),
        stats.get("VERY", 0),
        stats.get("INTERNAL", 0),
        stats.get("PASS", 0),
        errors,
        sent,
        elapsed,
        LIMITER.snapshot(),
    )

    return (
        error_ratio > 0.30
        or elapsed
        > SETTINGS.scan_interval * 1.25
    )


def performance() -> dict:
    rows = DBS.performance_summary()

    if not rows:
        return {
            "samples": 0,
            "note": (
                "Henüz tamamlanmış "
                "sinyal yok."
            ),
        }

    completed = [
        row
        for row in rows
        if row[6] is not None
    ]

    def group_stats(
        data: list,
    ) -> dict:
        done = [
            row
            for row in data
            if row[6] is not None
        ]

        if not done:
            return {
                "samples": len(data),
                "completed": 0,
            }

        values = [
            float(row[6])
            for row in done
        ]

        return {
            "samples": len(data),
            "completed": len(done),
            "avg_15m_pct": round(
                avg(values),
                2,
            ),
            "positive_15m_pct": round(
                sum(
                    value > 0
                    for value in values
                )
                / len(values)
                * 100,
                1,
            ),
        }

    result = {
        "samples": len(rows),
        "completed_15m": len(completed),
        "avg_max_pct": round(
            avg(
                [
                    float(row[3])
                    for row in rows
                ]
            ),
            2,
        ),
        "avg_min_pct": round(
            avg(
                [
                    float(row[4])
                    for row in rows
                ]
            ),
            2,
        ),
        "avg_15m_pct": (
            round(
                avg(
                    [
                        float(row[6])
                        for row in completed
                    ]
                ),
                2,
            )
            if completed
            else 0
        ),
    }

    result["score"] = {
        "68_75": group_stats(
            [
                row
                for row in rows
                if 68 <= row[0] < 76
            ]
        ),
        "76_83": group_stats(
            [
                row
                for row in rows
                if 76 <= row[0] < 84
            ]
        ),
        "84_90": group_stats(
            [
                row
                for row in rows
                if 84 <= row[0] < 91
            ]
        ),
        "91_100": group_stats(
            [
                row
                for row in rows
                if row[0] >= 91
            ]
        ),
    }

    result["level"] = {
        "BUY": group_stats(
            [
                row
                for row in rows
                if row[7] == "BUY"
            ]
        ),
        "VERY": group_stats(
            [
                row
                for row in rows
                if row[7] == "VERY"
            ]
        ),
    }

    result["entry_quality"] = {
        "0_49": group_stats(
            [
                row
                for row in rows
                if row[8] < 50
            ]
        ),
        "50_69": group_stats(
            [
                row
                for row in rows
                if 50 <= row[8] < 70
            ]
        ),
        "70_84": group_stats(
            [
                row
                for row in rows
                if 70 <= row[8] < 85
            ]
        ),
        "85_100": group_stats(
            [
                row
                for row in rows
                if row[8] >= 85
            ]
        ),
    }

    return result


def validate_market() -> None:
    info = CLIENT.exchange_info()

    if not isinstance(info, dict):
        raise RuntimeError(
            "exchangeInfo geçersiz"
        )

    raw_symbols = info.get(
        "symbols",
        [],
    )

    symbols = {
        item.get("symbol")
        for item in raw_symbols
        if isinstance(item, dict)
        and item.get("symbol")
    }

    try_count = sum(
        symbol.endswith("TRY")
        for symbol in symbols
    )

    if try_count <= 0:
        raise RuntimeError(
            f"BASE {SETTINGS.base_url} "
            "üzerinde TRY marketi bulunamadı."
        )

    if SETTINGS.market_symbol not in symbols:
        log.warning(
            "%s bulunamadı; "
            "BTC filtresi devre dışı.",
            SETTINGS.market_symbol,
        )

    log.info(
        "V23 | Binance TR doğrulandı | TRY:%d",
        try_count,
    )


def startup_message() -> None:
    if (
        not SETTINGS.telegram_token
        or not SETTINGS.telegram_chat
    ):
        log.warning(
            "Telegram ayarları eksik; "
            "Telegram bildirimleri kapalı."
        )
        return

    CLIENT.telegram(
        "🐋 BALİNA RADARI V23 AKTİF\n"
        "🏆 Öncelik sistemi aktif\n"
        "⚠️ TRAP filtresi aktif\n"
        "🛡️ Rate-limit koruması aktif"
    )


def loop() -> None:
    log.info(
        "🐋 BALİNA RADARI V23 başlatılıyor..."
    )

    if not SETTINGS.admin_token:
        log.warning(
            "ADMIN_TOKEN ayarlanmamış. "
            "/performance korumasız."
        )

    try:
        validate_market()
    except Exception:
        log.exception(
            "MARKET DOĞRULAMA HATASI"
        )
        return

    startup_message()

    last_cleanup = 0.0

    while True:
        started = time.monotonic()
        backoff = False

        try:
            backoff = scan()
        except Exception:
            log.exception(
                "Tarama döngüsü hatası"
            )
            backoff = True

        now = time.monotonic()

        if now - last_cleanup >= 86400:
            try:
                removed = (
                    DBS.cleanup_old_signals()
                )

                if removed:
                    log.info(
                        "Retention: %d eski "
                        "sinyal silindi.",
                        removed,
                    )

            except Exception:
                log.exception(
                    "Retention temizliği başarısız"
                )

            last_cleanup = now

        elapsed = (
            time.monotonic()
            - started
        )

        if backoff:
            delay = max(
                180,
                SETTINGS.scan_interval * 3,
            )
        else:
            delay = max(
                1,
                SETTINGS.scan_interval
                - elapsed,
            )

        time.sleep(delay)


app = Flask(__name__)


def require_admin(fn):
    @wraps(fn)
    def wrapper(
        *args,
        **kwargs,
    ):
        token = SETTINGS.admin_token

        if token:
            supplied = request.headers.get(
                "X-Admin-Token"
            )

            if supplied != token:
                abort(401)

        return fn(
            *args,
            **kwargs,
        )

    return wrapper


@app.route("/")
def home():
    return (
        "🐋 Balina Radarı V23 Aktif"
    )


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V23",
        "base": SETTINGS.base_url,
        "scan_interval": SETTINGS.scan_interval,
        "workers": SETTINGS.workers,
        "rate_limit": LIMITER.snapshot(),
    }


@app.route("/performance")
@require_admin
def performance_route():
    return performance()


def start_background_loop() -> None:
    worker = Thread(
        target=loop,
        daemon=True,
        name="balina-v23",
    )

    worker.start()


start_background_loop()


if __name__ == "__main__":
    port = int(
        os.getenv(
            "PORT",
            "8080",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
      )
