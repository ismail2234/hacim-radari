from __future__ import annotations

import os
import requests
from flask import Flask, jsonify

from config import Settings
from db import DB
from scoring import rank_signals

CFG = Settings()
app = Flask(__name__)


@app.get("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "BALİNA RADARI V24",
        "version": "24"
    })


@app.get("/health")
def health():
    return "OK", 200


def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        return r.ok
    except requests.RequestException:
        return False


def phase_text(phase):
    return {
        "PRE_BREAKOUT": "🟡 ÖNCÜ AL",
        "CONFIRMED": "🟢 AL TEYİDİ",
        "VERY": "🚀 GÜÇLÜ AL",
        "SETUP": "🟠 SETUP",
    }.get(phase, phase or "SİNYAL")


def signal_message(r, previous, movement_no):
    return (
        f"🐋 BALİNA RADARI V24\n\n"
        f"{phase_text(r.get('phase'))}\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r.get('price', 0):.8g}\n"
        f"💪 Güç: {r.get('score', 0)}/100\n"
        f"🏆 Öncelik: {r.get('priority', 0)}/100\n"
        f"📈 Bu hareket: #{movement_no}\n"
        f"📨 Daha önce: {previous} hareket\n"
        f"🔁 Teyit: {r.get('streak', 0)}x\n"
        f"📊 1m Hacim: {r.get('vr', 0):.2f}x | 5m: {r.get('vr5', 0):.2f}x\n"
        f"🚀 İvme: {r.get('impulse', 0):.2f}x\n"
        f"🛒 Alıcı: %{r.get('bp', 0):.0f}\n"
        f"🔢 İşlem: {r.get('trades_1m', 0)}\n"
        f"📈 RSI: {r.get('rv', 0):.0f} | ADX: {r.get('ad', 0):.0f}\n"
        f"🎯 Direnç: %{r.get('dist', 0):.2f}\n"
        f"🚀 Kırılım: {'✅' if r.get('breakout') else '❌'}"
    )


def record_signal(db, r):
    symbol = r["symbol"]
    phase = r.get("phase", "")
    history = db.get_signal_history(symbol, 1)

    if history and history[0].get("phase") in (
        "PRE_BREAKOUT", "CONFIRMED", "VERY"
    ):
        movement_no = int(history[0]["movement_no"])
        previous = max(0, movement_no - 1)
    else:
        previous = db.movement_count(symbol)
        movement_no = previous + 1

    db.add_signal(
        symbol,
        phase=phase,
        price=r.get("price"),
        score=r.get("score"),
    )
    return previous, movement_no


def process_signals(db, signals):
    for r in rank_signals(CFG, signals):
        if r.get("status") not in ("BUY", "VERY"):
            continue

        previous, movement_no = record_signal(db, r)
        telegram_send(signal_message(r, previous, movement_no))


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080"))
    )
    
