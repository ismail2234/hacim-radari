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
        f"🎯 Direnç: %{r.get('
