"""
V26 - main.py  (6/8 -- alerts + ana calisma noktasi birlesik)
Butun modulleri birlestiren ana calisma noktasi.

Kullanim:
    python main.py

Bu surum Telegram'a DOGRUDAN mesaj atar (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
Railway Variables'tan okunur). Sinyaller ayrica signals.jsonl dosyasina
da yazilir (yedek/log amacli).

Kurulum:
    pip install ccxt pandas numpy requests --break-system-packages
"""

import json
import os
import time
from datetime import datetime

import requests

from config import CONFIG
from market_data import fetch_ohlcv, get_tradable_symbols, get_24h_volume_try
from indicators import compute_all
from scoring import score_candidate, classify_score
from signal_engines import is_breakout_setup, confirm_breakout, find_recent_resistance, retest_status
from risk_and_trading import calculate_stop_loss, position_size, record_signal

SIGNALS_FILE = os.environ.get("SIGNALS_FILE", "signals.jsonl")


# ============================================================
# ALARM / SINYAL YAZMA (eski alerts.py)
# ============================================================

def format_alert_message(symbol: str, score_result: dict) -> str:
    total = score_result["total"]
    tag = classify_score(total)
    icon = "🔥" if total >= 80 else "🟢" if total >= 70 else "🟡" if total >= 60 else "⚪"

    b = score_result["breakdown"]
    return (
        f"{icon} {symbol} — {total}/100 ({tag})\n"
        f"Hacim:{b.get('hacim')} "
        f"Yapi:{b.get('fiyat_yapisi')} "
        f"Momentum:{b.get('momentum')} "
        f"MA:{b.get('ma_hizalanma')} "
        f"Vola:{b.get('volatilite')}"
    )


def send_telegram_alert(message: str):
    """Telegram Bot API ile dogrudan mesaj gonderir."""
    cfg = CONFIG["telegram"]
    if not cfg["bot_token"] or not cfg["chat_id"]:
        print("[TELEGRAM ALARM - token/chat_id tanimli degil, sadece konsola yazildi]")
        return

    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": cfg["chat_id"], "text": message}, timeout=10)
        if resp.status_code != 200:
            print(f"Telegram gonderim hatasi: {resp.status_code} {resp.text}")
    except Exception as e:
        print("Telegram gonderim hatasi:", e)


def write_signal(symbol: str, score_result: dict, message: str = None):
    """
    Sinyali hem Telegram'a gonderir hem de signals.jsonl dosyasina
    bir satir olarak ekler (yedek/log amacli).
    """
    message = message or format_alert_message(symbol, score_result)
    record = {
        "symbol": symbol,
        "score": score_result["total"],
        "tag": classify_score(score_result["total"]),
        "breakdown": score_result["breakdown"],
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
    }

    with open(SIGNALS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(message)
    send_telegram_alert(message)
    return record


# ============================================================
# PIYASA FILTRESI VE TARAMA
# ============================================================

def get_btc_trend() -> str:
    """Genel piyasa filtresi: BTC dususte ise altcoin sinyallerine ihtiyatli yaklas."""
    try:
        df = fetch_ohlcv(CONFIG["btc_symbol"], "4h", 50)
        df = compute_all(df)
        last = df.iloc[-1]
        if last["close"] > last["ma30"]:
            return "up"
        elif last["close"] < last["ma30"] * 0.97:
            return "down"
        return "sideways"
    except Exception as e:
        print("BTC trend alinamadi:", e)
        return "unknown"


def passes_liquidity_filter(symbol: str) -> bool:
    try:
        vol = get_24h_volume_try(symbol)
        return vol >= CONFIG["min_24h_volume_try"]
    except Exception:
        return True  # veri alinamazsa filtreyi engellemesin, sadece atlasin


def scan_symbol(symbol: str, market_trend: str, capital: float = 10000):
    df = fetch_ohlcv(symbol)
    df = compute_all(df)
    df.dropna(inplace=True)
    if len(df) < 50:
        return None

    result = score_candidate(df)

    threshold = CONFIG["score_thresholds"]["prep"]
    if market_trend == "down":
        threshold = 90

    if result["total"] < threshold:
        return None

    resistance = find_recent_resistance(df)
    breakout_confirmed = confirm_breakout(df, resistance)
    setup_ready = is_breakout_setup(df)
    r_status = retest_status(df, resistance)

    entry_price = float(df["close"].iloc[-1])
    stop = calculate_stop_loss(df, entry_price)
    size = position_size(capital, entry_price, stop)

    msg = format_alert_message(symbol, result)
    print(f"  kurulum_hazir:{setup_ready} kirilim_teyit:{breakout_confirmed} retest:{r_status}")
    print(f"  giris:{entry_price} stop:{stop} onerilen_boyut:{size}\n")

    write_signal(symbol, result, message=msg)
    record_signal(symbol, result["total"], entry_price, stop)

    return result


def scan_once(symbols: list, capital: float = 10000):
    market_trend = get_btc_trend()
    print(f"\n=== V26 Tarama | BTC trend: {market_trend} | {len(symbols)} coin ===\n")

    for symbol in symbols:
        try:
            if not passes_liquidity_filter(symbol):
                continue
            scan_symbol(symbol, market_trend, capital)
        except Exception as e:
            print(f"{symbol} taranamadi: {e}")
        time.sleep(0.3)


def run_loop(symbols: list, interval_seconds: int = 300, capital: float = 10000):
    """
    Surekli tarama dongusu (Railway worker icin).
    Tek bir taramada hata olsa bile worker'in tamamen cokup
    Railway'i sonsuz restart dongusune sokmamasi icin disari try/except konuldu.
    """
    while True:
        try:
            scan_once(symbols, capital)
        except Exception as e:
            print(f"Tarama dongusunde beklenmeyen hata: {e}")
        print(f"--- {interval_seconds} saniye bekleniyor ---")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    symbols = CONFIG["scan_symbols"]
    interval = CONFIG["scan_interval_sec"]
    capital = CONFIG["starting_capital"]

    print(f"V26 baslatiliyor | coinler: {symbols} | aralik: {interval}s")

    if not CONFIG["telegram"]["bot_token"] or not CONFIG["telegram"]["chat_id"]:
        print("UYARI: TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID tanimli degil, "
              "alarmlar sadece konsola/signals.jsonl'a yazilacak.")

    run_loop(symbols, interval_seconds=interval, capital=capital)
    
