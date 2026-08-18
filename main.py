from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread

from flask import Flask

from binance_client import BinanceClient
from config import SETTINGS
from db import DB
from market import MarketData
from rate_limiter import RateLimiter
from scoring import analyze, rank_signals


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

log = logging.getLogger("balina.v27")


SETTINGS.validate()


LIMITER = RateLimiter(
    SETTINGS.weight_budget_per_minute
)

CLIENT = BinanceClient(
    SETTINGS,
)
)

DBS = DB(
    SETTINGS.db_path,
    SETTINGS.signal_retention_days,
)

MARKET = MarketData(
    CLIENT,
    SETTINGS,
)


BAD_SYMBOLS = {
    "USDTTRY",
    "USDCTRY",
    "BUSDTRY",
    "FDUSDTRY",
    "TUSDTRY",
    "DAITRY",
}


def candidates(data):

    result = []

    for ticker in data:

        symbol = str(
            ticker.get(
                "symbol",
                "",
            )
        ).upper()

        if not symbol.endswith("TRY"):
            continue

        if symbol in BAD_SYMBOLS:
            continue

        if symbol in SETTINGS.excluded_symbols:
            continue

        try:

            volume = float(
                ticker.get(
                    "quoteVolume",
                    0,
                )
            )

            change = float(
                ticker.get(
                    "priceChangePercent",
                    0,
                )
            )

            price = float(
                ticker.get(
                    "lastPrice",
                    0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            continue

        if price <= 0:
            continue

        if volume < SETTINGS.min_quote_volume:
            continue

        if change > 25:
            continue

        result.append(
            {
                "symbol": symbol,
                "volume": volume,
                "chg": change,
                "price": price,
            }
        )

    result.sort(
        key=lambda x:
        x["volume"]
        * (
            1
            + max(
                x["chg"],
                0,
            )
            / 100
        ),
        reverse=True,
    )

    return result[
        :SETTINGS.shortlist_size
    ]


def analyze_one(item):

    try:

        return analyze(
            SETTINGS,
            CLIENT,
            DBS,
            MARKET,
            item,
        )

    except Exception as e:

        log.exception(
            "Analiz hatası | %s | %s",
            item.get(
                "symbol",
                "?",
            ),
            e,
        )

        return None


def telegram_message(r):

    status = r.get(
        "status",
        "PASS",
    )

    if status == "VERY":
        title = "🚀 GÜÇLÜ AL"
    elif status == "BUY":
        title = "🟢 AL"
    else:
        title = "🟡 ÖNCÜ"

    streak = int(
        r.get(
            "streak",
            1,
        ) or 1
    )

    if streak >= 3:
        confirm = f"🔥 {streak}. TEYİT"
    elif streak == 2:
        confirm = "✅ 2. TEYİT"
    else:
        confirm = "🔁 1. TEYİT"

    td = int(
        r.get(
            "td_setup",
            0,
        ) or 0
    )

    if td >= 13:
        td_text = "13 🔥"
    elif td >= 9:
        td_text = "9️⃣ 9"
    else:
        td_text = str(td)

    criteria = r.get(
        "criteria_list",
        r.get(
            "criteria",
            [],
        ),
    )

    if criteria:
        criteria_text = ", ".join(
            str(x)
            for x in criteria
        )
    else:
        criteria_text = "Yok"

    return (
        "🐋 BALİNA RADARI V27\n\n"

        f"{title} | {confirm}\n"
        f"🪙 #{r.get('symbol', '?')}\n"
        f"💰 {r.get('price', 0):.8g}\n"
        f"🎯 Skor: "
        f"{r.get('score', 0):.0f}/100\n\n"

        "☁️ ICHIMOKU\n"
        f"Trend: "
        f"{'YÜKSELİŞ ✅' if r.get('ichimoku_bullish') else 'ZAYIF'}\n"
        f"Bulut: "
        f"{'ÜSTÜNDE ✅' if r.get('above_cloud') else 'ALTINDA ❌'}\n\n"

        "📐 FIB\n"
        f"0.5: "
        f"{r.get('fib_0_5', 0):.8g}\n"
        f"0.618: "
        f"{r.get('fib_0_618', 0):.8g}\n"
        f"0.786: "
        f"{r.get('fib_0_786', 0):.8g}\n"
        f"Bölge: "
        f"{'✅' if r.get('fib_zone') else '⏳'}\n\n"

        "📊 VOLUME PROFILE\n"
        f"POC: "
        f"{r.get('poc', 0):.8g}\n"
        f"VA: "
        f"{r.get('va_low', 0):.8g}"
        f" - "
        f"{r.get('va_high', 0):.8g}\n"
        f"Fib + POC: "
        f"{'🔥 KESİŞİM' if r.get('fib_poc') else '⏳'}\n\n"

        f"9️⃣ TD: {td_text}\n\n"

        f"📊 Hacim: "
        f"{r.get('volume_ratio', 0):.2f}x\n"

        f"📈 RSI: "
        f"{r.get('rsi', 0):.1f}\n"

        f"📈 MACD: "
        f"{'✅' if r.get('macd') else '❌'}\n"

        f"📈 ADX: "
        f"{r.get('adx', 0):.1f}\n"

        f"💠 VWAP: "
        f"{'✅' if r.get('price_above_vwap') else '❌'}\n\n"

        f"🛑 Stop: "
        f"{r.get('stop_loss', 0):.8g}\n"

        f"📉 Risk: "
        f"%{r.get('stop_distance', 0):.2f}\n\n"

        f"🎯 Teyitler: "
        f"{criteria_text}\n\n"

        "⚠️ Yatırım tavsiyesi değildir."
    )
def scan():

    started = time.time()

    try:

        data = CLIENT.tickers()

        if not data:
            log.warning("Ticker yok.")
            return False

        items = candidates(data)

        if not items:
            log.info("Uygun TRY coin yok.")
            return False

        log.info(
            "Tarama | aday:%d",
            len(items),
        )

        results = []

        with ThreadPoolExecutor(
            max_workers=SETTINGS.workers
        ) as executor:

            futures = {
                executor.submit(
                    analyze_one,
                    item,
                ): item
                for item in items
            }

            for future in as_completed(
                futures
            ):

                try:

                    result = future.result()

                    if result:
                        results.append(result)

                except Exception as e:

                    log.exception(
                        "Worker hatası: %s",
                        e,
                    )

        signals = [
            x
            for x in results
            if x.get("status")
            in (
                "BUY",
                "VERY",
            )
        ]

        if not signals:

            log.info(
                "Sinyal yok."
            )

            return False

        try:

            ranked = rank_signals(
                signals,
                SETTINGS,
            )

        except TypeError:

            ranked = rank_signals(
                signals
            )

        ranked = ranked[
            :SETTINGS.max_signals
        ]

        sent = 0

        for result in ranked:

            symbol = result.get(
                "symbol",
                "",
            )

            level = result.get(
                "status",
                "PASS",
            )

            if level == "PASS":
                continue

            try:

                can_send = DBS.can_send(
                    symbol,
                    level,
                    SETTINGS.cooldown,
                )

            except Exception:

                log.exception(
                    "Cooldown hatası | %s",
                    symbol,
                )

                can_send = False

            if not can_send:

                log.info(
                    "Cooldown | %s",
                    symbol,
                )

                continue

            text = telegram_message(
                result
            )

            try:

                message_id = CLIENT.telegram(
                    text
                )

            except Exception:

                log.exception(
                    "Telegram hatası | %s",
                    symbol,
                )

                message_id = None

            if not message_id:
                continue

            sent += 1

            try:

                DBS.create_signal(
                    result
                )

            except Exception:

                log.exception(
                    "DB sinyal hatası | %s",
                    symbol,
                )

            try:

                DBS.put(
                    symbol=symbol,
                    score=result.get(
                        "score",
                        0,
                    ),
                    level=level,
                    stage=level,
                    sent=True,
                    streak=result.get(
                        "streak",
                        1,
                    ),
                    trap=False,
                    priority=result.get(
                        "priority",
                        0,
                    ),
                )

            except Exception:

                log.exception(
                    "DB state hatası | %s",
                    symbol,
                )

            log.info(
                "SİNYAL | %s | %s | %s",
                symbol,
                level,
                result.get(
                    "score",
                    0,
                ),
            )

        elapsed = (
            time.time()
            - started
        )

        log.info(
            "V27 | aday:%d | "
            "sonuç:%d | "
            "sinyal:%d | "
            "gönder:%d | "
            "%.1fs",
            len(items),
            len(results),
            len(signals),
            sent,
            elapsed,
        )

        return (
            elapsed
            > SETTINGS.scan_interval * 1.25
        )

    except Exception:

        log.exception(
            "SCAN genel hatası"
        )

        return True


def loop():

    log.info(
        "🐋 BALİNA RADARI V27 BAŞLADI"
    )

    try:

        info = CLIENT.exchange_info()

        symbols = {
            str(
                x.get(
                    "symbol",
                    "",
                )
            ).upper()
            for x in info.get(
                "symbols",
                [],
            )
        }

        try_count = sum(
            1
            for x in symbols
            if x.endswith("TRY")
            and x not in BAD_SYMBOLS
        )

        log.info(
            "Binance | TRY:%d",
            try_count,
        )

    except Exception:

        log.exception(
            "Binance kontrol hatası"
        )

    if (
        SETTINGS.telegram_token
        and SETTINGS.telegram_chat
    ):

        try:

            CLIENT.telegram(
                "🐋 BALİNA RADARI V27 AKTİF\n"
                "Ichimoku + Fibonacci + "
                "Volume Profile + TD Sequential"
            )

        except Exception:

            log.exception(
                "Telegram aktivasyon hatası"
            )

    last_cleanup = 0

    while True:

        started = time.time()

        try:

            backoff = scan()

        except Exception:

            log.exception(
                "Loop hatası"
            )

            backoff = True

        if (
            time.time()
            - last_cleanup
            > 86400
        ):

            try:
                DBS.cleanup_old_signals()
            except Exception:
                log.exception(
                    "DB temizlik hatası"
                )

            last_cleanup = time.time()

        elapsed = (
            time.time()
            - started
        )

        if backoff:

            wait = max(
                180,
                SETTINGS.scan_interval * 3,
            )

        else:

            wait = max(
                1,
                SETTINGS.scan_interval
                - elapsed,
            )

        log.info(
            "Sonraki tarama: %.0f sn",
            wait,
        )

        time.sleep(wait)


app = Flask(__name__)


@app.route("/")
def home():

    return (
        "🐋 Balina Radarı V27 Aktif"
    )


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V27",
        "scan_interval":
            SETTINGS.scan_interval,
        "workers":
            SETTINGS.workers,
    }


@app.route("/performance")
def performance():

    try:

        data = DBS.performance_summary()

        return {
            "status": "ok",
            "samples": len(data),
        }

    except Exception:

        return {
            "status": "ok",
            "samples": 0,
        }


Thread(
    target=loop,
    daemon=True,
    name="balina-v27",
).start()


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
