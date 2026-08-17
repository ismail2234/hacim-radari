from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread

from flask import Flask

from binance_client import BinanceClient
from config import SETTINGS, Settings
from db import DB
from market import MarketData
from rate_limiter import RateLimiter
from scoring import analyze, rank_signals


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

log = logging.getLogger("balina.main")


# ============================================================
# AYARLAR
# ============================================================

SETTINGS.validate()


# ============================================================
# ANA NESNELER
# ============================================================

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


# ============================================================
# ADAYLAR
# ============================================================

def candidates(
    cfg: Settings,
    data: list,
) -> list[dict]:

    result = []

    for ticker in data:

        symbol = str(
            ticker.get("symbol", "")
        ).upper()

        if (
            not symbol.endswith("TRY")
            or symbol in cfg.excluded_symbols
        ):
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

            if (
                volume < cfg.min_quote_volume
                or change > 25
                or price <= 0
            ):
                continue

            result.append(
                {
                    "symbol": symbol,
                    "volume": volume,
                    "chg": change,
                    "price": price,
                }
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

    return result


# ============================================================
# SHORTLIST
# ============================================================

def shortlist(
    cfg: Settings,
    items: list[dict],
) -> list[dict]:

    def ranking(item: dict) -> float:

        volume = float(
            item.get("volume", 0)
        )

        change = max(
            float(
                item.get("chg", 0)
            ),
            0.0,
        )

        return volume * (
            1.0 + change / 100.0
        )

    return sorted(
        items,
        key=ranking,
        reverse=True,
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

    status = r.get(
        "status",
        "PASS",
    )

    title = titles.get(
        status,
        status,
    )

    criteria = r.get(
        "criteria_list",
        [],
    )

    criteria_text = (
        "\n".join(
            f"• {item}"
            for item in criteria
        )
        if criteria
        else "• Teyit yok"
    )

    d30_value = r.get("d30")
    d90_value = r.get("d90")

    d30 = (
        f"{d30_value:+.1f}%"
        if d30_value is not None
        else "VERİ YOK"
    )

    d90 = (
        f"{d90_value:+.1f}%"
        if d90_value is not None
        else "VERİ YOK"
    )

    fakeout = ""

    if r.get("fakeout"):
        reasons = r.get(
            "fakeout_reasons",
            [],
        )

        fakeout = (
            "\n⚠️ FAKEOUT: "
            + (
                ", ".join(reasons)
                if reasons
                else "Teyit zayıf"
            )
            + "\n"
        )

    trap = ""

    if r.get("trap"):
        reasons = r.get(
            "trap_reasons",
            [],
        )

        trap = (
            "\n🚨 TUZAK: "
            + (
                ", ".join(reasons)
                if reasons
                else "Riskli yapı"
            )
            + "\n"
        )

    if r.get(
        "oi_available",
        False,
    ):
        oi = (
            f"{r.get('oi_change', 0):+.2f}%"
        )
    else:
        oi = "Yok"

    return (
        "🐋 BALİNA RADARI V26\n\n"
        f"{title}\n"
        "━━━━━━━━━━━━━━\n"
        f"🪙 #{r.get('symbol', '?')}\n"
        f"💰 Fiyat: "
        f"{r.get('price', 0):.8g}\n\n"
        f"💪 Skor: "
        f"{r.get('score', 0):.0f}/100\n"
        f"🏆 Öncelik: "
        f"{r.get('priority', 0):.0f}/100\n"
        f"🎯 Giriş kalitesi: "
        f"{r.get('entry_quality', 0):.0f}/100\n\n"

        "📐 MA YAPISI\n"
        f"MA7: {r.get('ma7', 0):.8g}\n"
        f"MA30: {r.get('ma30', 0):.8g}\n"
        f"MA99: {r.get('ma99', 0):.8g}\n"
        f"MA7 kırılımı: "
        f"{'✅' if r.get('ma7_cross') else '⏳'}\n\n"

        "📦 DARALMA\n"
        f"Daralma: "
        f"{'✅' if r.get('consolidation') else '❌'}\n"
        f"Aralık: "
        f"%{r.get('consolidation_range', 0):.2f}\n"
        f"BB genişliği: "
        f"%{r.get('bb_width', 0):.2f}\n\n"

        "🚀 KIRILIM\n"
        f"Kırılım: "
        f"{'✅' if r.get('breakout') else '⏳'}\n"
        f"Kapanmış mum: "
        f"{'✅' if r.get('closed_breakout') else '⏳'}\n"
        f"Direnç mesafesi: "
        f"%{r.get('dist', 0):.2f}\n\n"

        "📊 HACİM / ALICI\n"
        f"1m hacim: "
        f"{r.get('vr', 0):.2f}x\n"
        f"5m hacim: "
        f"{r.get('vr5', 0):.2f}x\n"
        f"İvme: "
        f"{r.get('impulse', 0):.2f}x\n"
        f"Alıcı baskısı: "
        f"%{r.get('bp', 0):.0f}\n"
        f"1m işlem: "
        f"{r.get('trades_1m', 0)}\n\n"

        "📈 MOMENTUM\n"
        f"RSI: {r.get('rv', 0):.1f}\n"
        f"ADX: {r.get('ad', 0):.1f}\n"
        f"ADX yükseliyor: "
        f"{'✅' if r.get('adx_rising') else '❌'}\n"
        f"MACD: "
        f"{'✅' if r.get('macd') else '❌'}\n\n"

        "🎯 TEYİTLER\n"
        f"{criteria_text}\n\n"

        f"💠 VWAP: "
        f"{'ÜZERİNDE ✅' if r.get('price_above_vwap') else 'ALTINDA'}\n"
        f"🛑 Stop referansı: "
        f"{r.get('stop_loss', 0):.8g}\n"
        f"📉 Stop mesafesi: "
        f"%{r.get('stop_distance', 0):.2f}\n\n"

        f"📅 30g: {d30} | 90g: {d90}\n"
        f"🌐 BTC/TRY: "
        f"{r.get('market_momentum', 0):+.2f}%\n\n"
        f"🔁 Teyit: "
        f"{r.get('streak', 0)}x\n"
        f"🕘 "
        f"{r.get('previous_signal', 'İlk sinyal')}\n"
        f"🟣 Open Interest: {oi}\n"
        f"{fakeout}"
        f"{trap}\n"
        "⚠️ Yatırım tavsiyesi değildir."
    )


# ============================================================
# TEK COIN ANALİZİ
# ============================================================

def analyze_one(item: dict) -> dict:

    try:
        result = analyze(
            SETTINGS,
            CLIENT,
            DBS,
            MARKET,
            item,
        )

        return result or {
            "symbol": item.get(
                "symbol",
                "?",
            ),
            "status": "PASS",
        }

    except Exception as e:

        log.exception(
            "Analiz hatası | %s | %s",
            item.get("symbol", "?"),
            e,
        )

        return {
            "symbol": item.get(
                "symbol",
                "?",
            ),
            "status": "PASS",
            "error": str(e),
        }


# ============================================================
# TARAMA
# ============================================================

def scan() -> bool:

    started = time.time()

    stats = {
        "ONCU": 0,
        "BUY": 0,
        "VERY": 0,
        "PASS": 0,
        "error": 0,
    }

    sent = 0

    try:

        all_candidates = CLIENT.tickers()

        if not all_candidates:
            log.warning(
                "Ticker verisi alınamadı."
            )
            return True

        items = candidates(
            SETTINGS,
            all_candidates,
        )

        if not items:
            log.info(
                "Uygun TRY coin bulunamadı."
            )
            return False

        items = shortlist(
            SETTINGS,
            items,
        )

        try_count = sum(
            1
            for x in all_candidates
            if str(
                x.get(
                    "symbol",
                    "",
                )
            ).upper().endswith("TRY")
        )

        log.info(
            "Tarama başladı | "
            "TRY:%d | Shortlist:%d",
            try_count,
            len(items),
        )

        results = []

        with ThreadPoolExecutor(
            max_workers=SETTINGS.workers,
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

                item = futures[future]

                try:

                    result = future.result()

                    if result:
                        results.append(
                            result
                        )

                except Exception as e:

                    stats["error"] += 1

                    log.exception(
                        "Worker hatası | %s | %s",
                        item.get(
                            "symbol",
                            "?",
                        ),
                        e,
                    )
