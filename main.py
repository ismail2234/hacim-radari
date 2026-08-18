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
    LIMITER,
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
