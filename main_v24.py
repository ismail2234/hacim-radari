from __future__ import annotations

import os
import time

import requests

from config import Settings
from db import DB
from scoring import analyze, rank_signals
from binance_client import BinanceClient
from market import MarketData


def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    return r.ok


def phase_text(phase):
    return {
        "PRE_BREAKOUT": "🟡 ÖNCÜ AL",
        "CONFIRMED": "🟢 AL TEYİDİ",
        "VERY": "🚀 GÜÇLÜ AL",
        "SETUP": "🟠 SETUP",
    }.get(phase, phase)


def signal_message(r, previous, movement_no):
    return (
        f"🐋 BALİNA RADARI V24\n\n"
        f"{phase_text(r.get('phase', ''))}\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r['price']:.8g}\n"
        f"💪 Güç: {r['score']}/100\n"
        f"🏆 Öncelik: {r.get('priority', 0)}/100\n"
        f"📈 Bu hareket: #{movement_no}\n"
        f"📨 Daha önce: {previous} hareket\n"
        f"🔁 Streak: {r.get('streak', 0)}x\n"
        f"📊 1m Hacim: {r.get('vr', 0):.2f}x | "
        f"5m: {r.get('vr5', 0):.2f}x\n"
        f"🚀 İvme: {r.get('impulse', 0):.2f}x\n"
        f"🛒 Alıcı: %{r.get('bp', 0):.0f}\n"
        f"🔢 İşlem: {r.get('trades_1m', 0)}\n"
        f"📈 RSI: {r.get('rv', 0):.0f} | "
        f"ADX: {r.get('ad', 0):.0f}\n"
        f"🎯 Direnç: %{r.get('dist', 0):.2f}\n"
        f"🚀 Kırılım: {'✅' if r.get('breakout') else '❌'}"
    )


def record_signal(db, r):
    symbol = r["symbol"]
    phase = r.get("phase", "")
    previous = db.previous_signals(symbol)

    movement_no = previous + 1

    # Aynı hareket içinde aşama değişimi varsa yeni hareket sayma.
    history = db.get_signal_history(symbol, 1)
    if history:
        last = history[0]
        last_phase = last.get("phase")
        if last_phase in ("PRE_BREAKOUT", "CONFIRMED", "VERY"):
            movement_no = int(last["movement_no"])

    db.add_signal(
        symbol,
        phase=phase,
        price=r.get("price"),
        score=r.get("score"),
    )

    return previous, movement_no


def process_signals(db, signals):
    signals = rank_signals(CFG, signals)

    for r in signals:
        if r.get("status") not in ("BUY", "VERY"):
            continue

        previous, movement_no = record_signal(db, r)

        r["previous_signals"] = previous
        r["movement_no"] = movement_no

        telegram_send(
            signal_message(
                r,
                previous,
                movement_no,
            )
        )


def main():
    cfg = Settings()
    db = DB()
    client = BinanceClient(cfg)
    market = MarketData(client)

    # V23'ün mevcut market taramasını kullanmak için
    # bu noktada mevcut main.py'deki item üretimini bağlayın.
    #
    # Bu dosya V24 Telegram/DB katmanını hazırlar.
    raise RuntimeError(
        "V24 ana akışı hazır. Mevcut V23 main.py'deki item taramasını "
        "process_signals() fonksiyonuna bağlayın."
    )


CFG = Settings()


if __name__ == "__main__":
    main()
    
