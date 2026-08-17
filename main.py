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

LIMITER = RateLimiter(
    SETTINGS.weight_budget_per_minute
)

CLIENT = BinanceClient(
    SETTINGS,
    LIMITER
)

DBS = DB(
    SETTINGS.db_path,
    retention_days=SETTINGS.signal_retention_days
)

MARKET = MarketData(
    CLIENT,
    SETTINGS
)


# ============================================================
# ADAYLAR
# ============================================================

def candidates(
    cfg: Settings,
    data: list
) -> list[dict]:

    result = []

    for ticker in data:

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

            if (
                volume < cfg.min_quote_volume
                or change > 25
                or price <= 0
            ):
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


def shortlist(
    cfg: Settings,
    items: list[dict]
) -> list[dict]:

    def ranking(item):

        return (
            item["volume"]
            * (1 + max(item["chg"], 0) / 100)
        )

    return sorted(
        items,
        key=ranking,
        reverse=True
    )[:cfg.shortlist_size]


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def message(r: dict) -> str:

    titles = {
        "ONCU": "🟡 ÖNCÜ AL",
        "BUY": "🟢 AL",
        "VERY": "🚀 GÜÇLÜ AL",
    }

    title = titles.get(
        r["status"],
        r["status"]
    )

    criteria = r.get(
        "criteria_list",
        []
    )

    criteria_text = (
        "\n".join(
            f"• {x}" for x in criteria
        )
        if criteria
        else "• Teyit yok"
    )

    d30 = (
        f"{r['d30']:+.1f}%"
        if r.get("d30") is not None
        else "VERİ YOK"
    )

    d90 = (
        f"{r['d90']:+.1f}%"
        if r.get("d90") is not None
        else "VERİ YOK"
    )

    previous = r.get(
        "previous_signal",
        "İlk sinyal"
    )

    fakeout = ""

    if r.get("fakeout"):
        fakeout = (
            "\n⚠️ FAKEOUT: "
            + ", ".join(
                r.get(
                    "fakeout_reasons",
                    []
                )
            )
            + "\n"
        )

    trap = ""

    if r.get("trap"):
        trap = (
            "\n🚨 TUZAK: "
            + ", ".join(
                r.get(
                    "trap_reasons",
                    []
                )
            )
            + "\n"
        )

    oi = "Yok"

    if r.get("oi_available"):
        oi = (
            f"{r.get('oi_change', 0):+.2f}%"
        )

    return (
        "🐋 BALİNA RADARI V26\n\n"

        f"{title}\n"
        "━━━━━━━━━━━━━━\n"

        f"🪙 #{r['symbol']}\n"
        f"💰 Fiyat: {r['price']:.8g}\n\n"

        f"💪 Skor: {r['score']}/100\n"
        f"🏆 Öncelik: {r['priority']:.0f}/100\n"
        f"🎯 Giriş kalitesi: "
        f"{r['entry_quality']}/100\n\n"

        "📐 MA YAPISI\n"
        f"MA7: {r['ma7']:.8g}\n"
        f"MA30: {r['ma30']:.8g}\n"
        f"MA99: {r['ma99']:.8g}\n"
        f"MA7 kırılımı: "
        f"{'✅' if r['ma7_cross'] else '⏳'}\n\n"

        "📦 DARALMA\n"
        f"Daralma: "
        f"{'✅' if r['consolidation'] else '❌'}\n"
        f"Aralık: %{r['consolidation_range']:.2f}\n"
        f"BB genişliği: %{r['bb_width']:.2f}\n\n"

        "🚀 KIRILIM\n"
        f"Kırılım: "
        f"{'✅' if r['breakout'] else '⏳'}\n"
        f"Kapanmış mum: "
        f"{'✅' if r['closed_breakout'] else '⏳'}\n"
        f"Direnç mesafesi: "
        f"%{r['dist']:.2f}\n\n"

        "📊 HACİM / ALICI\n"
        f"1m hacim: {r['vr']:.2f}x\n"
        f"5m hacim: {r['vr5']:.2f}x\n"
        f"İvme: {r['impulse']:.2f}x\n"
        f"Alıcı baskısı: %{r['bp']:.0f}\n"
        f"1m işlem: {r['trades_1m']}\n\n"

        "📈 MOMENTUM\n"
        f"RSI: {r['rv']:.1f}\n"
        f"ADX: {r['ad']:.1f}\n"
        f"ADX yükseliyor: "
        f"{'✅' if r['adx_rising'] else '❌'}\n"
        f"MACD: "
        f"{'✅' if r['macd'] else '❌'}\n\n"

        "🎯 TEYİTLER\n"
        f"{criteria_text}\n\n"

        f"💠 VWAP: "
        f"{'ÜZERİNDE ✅' if r['price_above_vwap'] else 'ALTINDA'}\n"

        f"🛑 Stop referansı: "
        f"{r['stop_loss']:.8g}\n"
        f"📉 Stop mesafesi: "
        f"%{r['stop_distance']:.2f}\n\n"

        f"📅 30g: {d30} | 90g: {d90}\n"
        f"🌐 BTC/TRY: "
        f"{r['market_momentum']:+.2f}%\n\n"

        f"🔁 Teyit: {r['streak']}x\n"
        f"🕘 {previous}\n"
        f"🟣 Open Interest: {oi}\n"

        f"{fakeout}"
        f"{trap}\n"

        "⚠️ Yatırım tavsiyesi değildir."
    )


# ============================================================
# TARAMA
# ============================================================

def scan() -> bool:

    started = time.time()

    data = CLIENT.tickers()

    if not data:
        log.warning("Ticker verisi alınamadı.")
        return True

    price_map = {}

    for item in data:

        try:
            price_map[
                item.get("symbol")
            ] = float(
                item.get("lastPrice", 0)
            )

        except (TypeError, ValueError):
            continue

    DBS.update_outcomes(
        price_map,
        SETTINGS.outcome_window
    )

    all_candidates = candidates(
        SETTINGS,
        data
    )

    items = shortlist(
        SETTINGS,
        all_candidates
    )

    signals = []
    stats = {}

    with ThreadPoolExecutor(
        max_workers=SETTINGS.workers
    ) as executor:

        jobs = [
            executor.submit(
                analyze,
                SETTINGS,
                CLIENT,
                DBS,
                MARKET,
                item
            )
            for item in items
        ]

        for job in as_completed(jobs):

            try:
                result = job.result()

            except Exception:

                result = {
                    "status": "error"
                }

            status = result.get(
                "status",
                "error"
            )

            stats[status] = (
                stats.get(status, 0) + 1
            )

            if status in (
                "ONCU",
                "BUY",
                "VERY",
            ):
                signals.append(result)

    signals = rank_signals(
        SETTINGS,
        signals
    )

    sent = 0

    for r in signals:

        if sent >= SETTINGS.max_signals:
            break

        if r["priority"] < SETTINGS.min_priority:
            continue

        if not DBS.can_send(
            r["symbol"],
            r["status"],
            SETTINGS.cooldown
        ):
            continue

        if CLIENT.telegram(
            message(r)
        ):

            DBS.put(
                r["symbol"],
                r["score"],
                r["status"],
                r["status"],
                sent=time.time(),
                streak=r["streak"],
                trap=r["trap"],
                priority=r["priority"],
            )

            DBS.create_signal(r)

            sent += 1

        time.sleep(0.3)

    elapsed = time.time() - started

    log.info(
        "V26 | TRY:%d/%d | "
        "ONCU:%d | AL:%d | VERY:%d | "
        "Hata:%d | Gönder:%d | %.1fs",
        len(items),
        len(all_candidates),
        stats.get("ONCU", 0),
        stats.get("BUY", 0),
        stats.get("VERY", 0),
        stats.get("error", 0),
        sent,
        elapsed,
    )

    return (
        stats.get("error", 0)
        / max(1, len(items))
        > 0.30
        or elapsed
        > SETTINGS.scan_interval * 1.25
    )


# ============================================================
# LOOP
# ============================================================

def loop():

    log.info(
        "🐋 BALİNA RADARI V26 başlatılıyor..."
    )

    try:

        info = CLIENT.exchange_info()

        symbols = {
            x.get("symbol")
            for x in info.get(
                "symbols",
                []
            )
        }

        try_count = sum(
            s.endswith("TRY")
            for s in symbols
            if s
        )

        if try_count <= 0:
            raise RuntimeError(
                "TRY market bulunamadı."
            )

        log.info(
            "Binance doğrulandı | TRY:%d",
            try_count
        )

    except Exception as e:

        log.exception(
            "MARKET DOĞRULAMA HATASI: %s",
            e
        )

        return

    if (
        SETTINGS.telegram_token
        and SETTINGS.telegram_chat
    ):

        CLIENT.telegram(
            "🐋 BALİNA RADARI V26 AKTİF\n\n"
            "📐 MA7 / MA30 / MA99\n"
            "📦 Daralma + sıkışma\n"
            "🚀 Kapanmış mum kırılımı\n"
            "📊 Hacim + alıcı baskısı\n"
            "📈 RSI + MACD + ADX\n"
            "🛡️ Fakeout filtresi\n"
            "🔁 Tekrar sinyal takibi"
        )

    last_cleanup = 0

    while True:

        started = time.time()

        try:
            backoff = scan()

        except Exception:

            log.exception(
                "Tarama hatası"
            )

            backoff = True

        if started - last_cleanup > 86400:

            try:

                removed = (
                    DBS.cleanup_old_signals()
                )

                if removed:
                    log.info(
                        "%d eski sinyal silindi.",
                        removed
                    )

            except Exception:

                log.exception(
                    "DB temizlik hatası"
                )

            last_cleanup = started

        elapsed = time.time() - started

        if backoff:

            time.sleep(
                max(
                    180,
                    SETTINGS.scan_interval * 3
                )
            )

        else:

            time.sleep(
                max(
                    1,
                    SETTINGS.scan_interval - elapsed
                )
            )


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():

    return (
        "🐋 Balina Radarı V26 Aktif"
    )


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V26",
        "scan_interval":
            SETTINGS.scan_interval,
        "workers":
            SETTINGS.workers,
        "rate_limit":
            LIMITER.snapshot(),
    }


@app.route("/performance")
def performance():

    return {
        "samples":
            len(DBS.performance_summary())
    }


Thread(
    target=loop,
    daemon=True,
    name="balina-v26"
).start()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),
        use_reloader=False,
    )
