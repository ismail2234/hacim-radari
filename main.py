import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread

from flask import Flask

from config import SETTINGS
from binance_client import BinanceClient
from scoring import analyze, rank_signals


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("v27")

client = BinanceClient(SETTINGS)


def get_try_symbols():

    info = client.exchange_info()

    symbols = []

    for item in info.get("symbols", []):

        symbol = str(
            item.get("symbol", "")
        ).upper()

        status = str(
            item.get("status", "")
        ).upper()

        if (
            symbol.endswith("TRY")
            and status in (
                "",
                "TRADING",
            )
        ):
            symbols.append(symbol)

    return symbols


def get_candidates():

    tickers = client.tickers()

    result = []

    for ticker in tickers:

        symbol = str(
            ticker.get("symbol", "")
        ).upper()

        if not symbol.endswith("TRY"):
            continue

        try:

            volume = float(
                ticker.get(
                    "quoteVolume",
                    0,
                )
            )

            price = float(
                ticker.get(
                    "lastPrice",
                    0,
                )
            )

            change = float(
                ticker.get(
                    "priceChangePercent",
                    0,
                )
            )

        except:

            continue

        if price <= 0:
            continue

        if volume < SETTINGS.min_volume:
            continue

        if change > 25:
            continue

        result.append(
            {
                "symbol": symbol,
                "volume": volume,
                "change": change,
                "price": price,
            }
        )

    result.sort(
        key=lambda x: (
            x["volume"]
            * (
                1
                + max(
                    x["change"],
                    0,
                ) / 100
            )
        ),
        reverse=True,
    )

    return result[
        :SETTINGS.shortlist
    ]


def analyze_one(item):

    try:

        return analyze(
            SETTINGS,
            client,
            item["symbol"],
        )

    except Exception as e:

        log.error(
            "%s: %s",
            item["symbol"],
            e,
        )

        return None


def format_message(r):

    status = {
        "BUY": "🟢 AL",
        "VERY": "🚀 GÜÇLÜ AL",
    }.get(
        r.get("status"),
        "🟡 ÖNCÜ",
    )

    streak = r.get(
        "streak",
        1,
    )

    if streak >= 3:
        teyit = f"🔥 {streak}. TEYİT"
    elif streak == 2:
        teyit = "✅ 2. TEYİT"
    else:
        teyit = "🔁 1. TEYİT"

    td = r.get(
        "td_setup",
        0,
    )

    if td >= 13:
        td_text = "13 🔥"
    elif td >= 9:
        td_text = "9 ✅"
    else:
        td_text = str(td)

    return (
        "🐋 BALİNA RADARI V27\n\n"

        f"{status} | {teyit}\n"
        f"🪙 #{r.get('symbol', '?')}\n"
        f"💰 {r.get('price', 0):.8g}\n\n"

        f"🎯 Skor: "
        f"{r.get('score', 0)}/100\n\n"

        "☁️ ICHIMOKU\n"
        f"Trend: "
        f"{'YÜKSELİŞ ✅' if r.get('ichimoku_bullish') else 'ZAYIF ❌'}\n"
        f"Bulut: "
        f"{'ÜSTÜNDE ✅' if r.get('above_cloud') else 'ALTINDA ❌'}\n\n"

        "📐 FIB\n"
        f"0.5: {r.get('fib_050', 0):.8g}\n"
        f"0.618: {r.get('fib_0618', 0):.8g}\n"
        f"0.786: {r.get('fib_0786', 0):.8g}\n"
        f"Bölge: "
        f"{'✅' if r.get('fib_zone') else '⏳'}\n\n"

        "📊 VOLUME PROFILE\n"
        f"POC: {r.get('poc', 0):.8g}\n"
        f"VA: {r.get('value_low', 0):.8g}"
        f" - {r.get('value_high', 0):.8g}\n"
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
        f"{'✅' if r.get('vwap_ok') else '❌'}\n\n"

        f"🛑 Stop: "
        f"{r.get('stop', 0):.8g}\n"

        f"📉 Risk: "
        f"%{r.get('stop_distance', 0):.2f}\n\n"

        f"🎯 Teyitler: "
        f"{', '.join(r.get('criteria', []))}\n\n"

        "⚠️ Yatırım tavsiyesi değildir."
    )


def scan():

    candidates = get_candidates()

    if not candidates:

        log.info(
            "TRY aday bulunamadı."
        )

        return

    log.info(
        "Tarama | TRY aday: %d",
        len(candidates),
    )

    results = []

    with ThreadPoolExecutor(
        max_workers=SETTINGS.workers
    ) as executor:

        futures = [
            executor.submit(
                analyze_one,
                item,
            )
            for item in candidates
        ]

        for future in as_completed(
            futures
        ):

            try:

                result = future.result()

                if result:
                    results.append(
                        result
                    )

            except Exception as e:

                log.error(
                    "Analiz: %s",
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

        return

    ranked = rank_signals(
        signals
    )

    ranked = ranked[
        :SETTINGS.max_signals
    ]

    for result in ranked:

        text = format_message(
            result
        )

        if client.telegram(text):

            log.info(
                "Telegram | %s | %s",
                result.get("symbol"),
                result.get("score"),
            )


def worker():

    log.info(
        "🐋 BALİNA RADARI V27 BAŞLADI"
    )

    while True:

        started = time.time()

        try:

            symbols = get_try_symbols()

            log.info(
                "Binance TRY market: %d",
                len(symbols),
            )

            if symbols:
                scan()

        except Exception as e:

            log.exception(
                "Tarama hatası: %s",
                e,
            )

        elapsed = (
            time.time()
            - started
        )

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

    return "Balina Radarı V27 Aktif"


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V27",
    }


Thread(
    target=worker,
    daemon=True,
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
    )
