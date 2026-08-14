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

if not SETTINGS.admin_token:
    log.warning(
        "ADMIN_TOKEN ayarlanmamış. /performance endpoint'i korumasız."
    )

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


def candidates(cfg: Settings, data: list) -> list[dict]:
    result = []

    for ticker in data:
        if not isinstance(ticker, dict):
            continue

        symbol = ticker.get("symbol", "")

        if not symbol.endswith("TRY"):
            continue

        if symbol in cfg.excluded_symbols:
            continue

        try:
            volume = float(ticker.get("quoteVolume", 0))
            change = float(ticker.get("priceChangePercent", 0))
            price = float(ticker.get("lastPrice", 0))
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
    items: list[dict]
) -> list[dict]:

    def rank(item: dict) -> float:
        return item["volume"] * (
            1 + max(item["chg"], 0) / 100
        )

    return sorted(
        items,
        key=rank,
        reverse=True
    )[:cfg.shortlist_size]


def message(r: dict) -> str:
    title = (
        "🔥 ÇOK GÜÇLÜ AL"
        if r["status"] == "VERY"
        else "🟢 AL"
    )

    reasons = []

    if r["closed_breakout"]:
        reasons.append("Kapanış kırılımı")
    elif r["breakout"]:
        reasons.append("Direnç kırıldı")
    elif r["dist"] <= 0.35:
        reasons.append(
            f"Direnç %{r['dist']:.2f}"
        )

    if r["vr"] >= 1.5:
        reasons.append(
            f"1m hacim {r['vr']:.1f}x"
        )

    if r["vr5"] >= 1.5:
        reasons.append(
            f"5m hacim {r['vr5']:.1f}x"
        )

    if r["impulse"] >= 2:
        reasons.append(
            f"İvme {r['impulse']:.1f}x"
        )

    if r["bp"] >= 65:
        reasons.append(
            f"Alıcı %{r['bp']:.0f}"
        )

    if r["ema"]:
        reasons.append("EMA trend")

    if r["macd"]:
        reasons.append("MACD güçleniyor")

    if r["hl"]:
        reasons.append("Higher-Low")

    if r["squeeze"]:
        reasons.append("BB sıkışma")

    if r["trades_1m"] >= SETTINGS.min_1m_trades:
        reasons.append("İşlem katılımı güçlü")

    trap = ""

    if r["trap"]:
        trap = (
            "\n⚠️ TUZAK: "
            + ", ".join(r["trap_reasons"])
            + "\n"
        )

    if r["status"] == "VERY":
        result = "🚀 Güçlü teyit."
    elif r["closed_breakout"]:
        result = "🎯 Alım teyidi oluştu."
    else:
        result = "🟡 Kırılım teyidi bekleniyor."

    d30 = (
        f"{r['d30']:+.1f}%"
        if r["d30"] is not None
        else "VERİ YOK"
    )

    d90 = (
        f"{r['d90']:+.1f}%"
        if r["d90"] is not None
        else "VERİ YOK"
    )

    return (
        "🐋 BALİNA RADARI V23\n\n"
        f"{title}\n\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r['price']:.8g}\n"
        f"💪 Güç: {r['score']}/100\n"
        f"🏆 Öncelik: {r['priority']:.0f}/100\n"
        f"🎯 Giriş: {r['entry_quality']}/100\n"
        f"🔁 Teyit: {r['streak']}x\n\n"
        f"📊 1m Hacim: {r['vr']:.2f}x"
        f" | 5m: {r['vr5']:.2f}x\n"
        f"🚀 İvme: {r['impulse']:.2f}x\n"
        f"🛒 Alıcı: %{r['bp']:.0f}\n"
        f"🔢 İşlem: {r['trades_1m']}\n"
        f"📈 RSI: {r['rv']:.0f}"
        f" | ADX: {r['ad']:.0f}\n"
        f"🎯 Direnç: %{r['dist']:.2f}\n"
        f"🚀 Kırılım: "
        f"{'✅' if r['breakout'] else '⏳'}\n"
        f"📅 30g: {d30} | 
