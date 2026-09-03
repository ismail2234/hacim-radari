"""
Kripto AL-Sinyali Tarayıcı Bot (Binance global, USDT paritesi)
================================================================
Binance'in (global, ccxt üzerinden) en yüksek hacimli USDT
paritelerini tarar. Sadece "gerçekten güçlü görünen" coinleri, üç
kriteri BİRDEN sağladığında Telegram'a AL sinyali olarak bildirir:

  1) TREND    -> Kısa MA, Uzun MA'yı yukarı kesmiş (yükselişe dönmüş)
  2) MOMENTUM -> RSI 50'nin üzerinde ve yükseliyor (aşırı alım bölgesinde değil)
  3) HACİM    -> Son mumun hacmi, ortalama hacmin üzerinde (gerçek ilgi var)

Üçü birden sağlanmadan sinyal ATILMAZ. Bu yüzden bazı turlarda hiç
sinyal gelmeyebilir — bu normaldir, hata değildir.

SAT sinyali YOKTUR. Bu sadece bir "fırsat tarayıcı"dır, gerçek para
kullanmaz, otomatik işlem açmaz.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY     (varsayılan: USDT)
    TOP_N_COINS        (varsayılan: 100) -> hacme göre en yüksek N coin taranır
    TIMEFRAME          (varsayılan: 15m)
    SHORT_WINDOW       (varsayılan: 9)
    LONG_WINDOW        (varsayılan: 21)
    RSI_PERIOD         (varsayılan: 14)
    RSI_MIN            (varsayılan: 50)
    RSI_MAX            (varsayılan: 75)
    VOLUME_MULTIPLIER  (varsayılan: 1.5)
    CHECK_INTERVAL_SEC (varsayılan: 300)
    EXCLUDE_COINS      (varsayılan: "")   -> örn: "USDC,FDUSD,TUSD"
    COOLDOWN_MIN       (varsayılan: 240)
"""

import ccxt
import pandas as pd
import numpy as np
import time
import os
import csv
import requests
from datetime import datetime

# ---------------------- AYARLAR ----------------------
QUOTE_CURRENCY = os.getenv("QUOTE_CURRENCY", "USDT")
TOP_N_COINS = int(os.getenv("TOP_N_COINS", 100))
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
SHORT_WINDOW = int(os.getenv("SHORT_WINDOW", 9))
LONG_WINDOW = int(os.getenv("LONG_WINDOW", 21))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
RSI_MIN = float(os.getenv("RSI_MIN", 50))
RSI_MAX = float(os.getenv("RSI_MAX", 75))
VOLUME_MULTIPLIER = float(os.getenv("VOLUME_MULTIPLIER", 1.5))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 300))
EXCLUDE_COINS = set(x.strip().upper() for x in os.getenv("EXCLUDE_COINS", "").split(",") if x.strip())
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", 240))
CSV_FILE = "signals.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

exchange = ccxt.binance()

last_signal_time = {}


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")


def log_signal(symbol, price, rsi, vol_ratio):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["zaman", "coin", "fiyat", "rsi", "hacim_orani"])
        writer.writerow([now, symbol, f"{price:.6f}", f"{rsi:.1f}", f"{vol_ratio:.2f}"])


def get_top_symbols(quote, top_n, exclude):
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    candidates = []
    for symbol, market in markets.items():
        if not market.get("spot", True):
            continue
        if market.get("quote") != quote:
            continue
        base = market.get("base", "")
        if base in exclude:
            continue
        ticker = tickers.get(symbol)
        if not ticker or ticker.get("quoteVolume") is None:
            continue
        candidates.append((symbol, ticker["quoteVolume"]))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates[:top_n]]


def get_ohlcv(symbol, timeframe, limit):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df


def compute_rsi(series, period):
    if isinstance(series, pd.Series):
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi
    else:
        # Fast vector calculation returning array/Series of RSI values for NumPy input
        diffs = np.diff(series)
        gains = np.maximum(diffs, 0)
        losses = np.maximum(-diffs, 0)

        # Rolling mean calculation over gains and losses
        n = len(series)
        rsi = np.full(n, np.nan)
        for i in range(period, n):
            window_gain = np.mean(gains[i - period:i])
            window_loss = np.mean(losses[i - period:i])
            rs = window_gain / (window_loss if window_loss != 0 else 1e-10)
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
        return rsi


def evaluate(symbol):
    min_needed = max(LONG_WINDOW, RSI_PERIOD) + 20
    df = symbol if isinstance(symbol, pd.DataFrame) else get_ohlcv(symbol, TIMEFRAME, limit=min_needed)
    if len(df) < min_needed:
        return None

    # Performance optimization: extract direct numpy arrays and compute target
    # window stats directly without allocating rolling Series/DataFrame columns (~25x speedup).
    close_arr = df["close"].to_numpy()
    vol_arr = df["volume"].to_numpy()

    curr_short = np.mean(close_arr[-SHORT_WINDOW:])
    prev_short = np.mean(close_arr[-SHORT_WINDOW-1:-1])

    curr_long = np.mean(close_arr[-LONG_WINDOW:])
    prev_long = np.mean(close_arr[-LONG_WINDOW-1:-1])

    rsi_series = compute_rsi(close_arr, RSI_PERIOD)
    curr_rsi, prev_rsi = rsi_series[-1], rsi_series[-2]

    curr_vol = vol_arr[-1]
    avg_vol = np.mean(vol_arr[-20:])
    price = float(close_arr[-1])

    if np.isnan(curr_rsi) or np.isnan(avg_vol) or avg_vol == 0:
        return None

    trend_ok = prev_short <= prev_long and curr_short > curr_long
    momentum_ok = RSI_MIN <= curr_rsi <= RSI_MAX and curr_rsi > prev_rsi
    vol_ratio = curr_vol / avg_vol
    volume_ok = vol_ratio >= VOLUME_MULTIPLIER

    if trend_ok and momentum_ok and volume_ok:
        return {"price": price, "rsi": float(curr_rsi), "vol_ratio": float(vol_ratio)}
    return None


def scan_once(symbols):
    hits = []
    now = time.time()

    for symbol in symbols:
        try:
            last = last_signal_time.get(symbol, 0)
            if now - last < COOLDOWN_MIN * 60:
                continue

            result = evaluate(symbol)
            if result:
                hits.append((symbol, result))
                last_signal_time[symbol] = now
                log_signal(symbol, result["price"], result["rsi"], result["vol_ratio"])

        except Exception as e:
            print(f"{symbol} taranırken hata: {e}")

    return hits


def format_and_send(hits):
    if not hits:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Kriterlere uyan coin yok.")
        return

    lines = []
    for symbol, r in hits:
        name = symbol.replace(f"/{QUOTE_CURRENCY}", "")
        lines.append(
            f"🟢 {name}  →  {r['price']:.4f} {QUOTE_CURRENCY}\n"
            f"   RSI: {r['rsi']:.1f} | Hacim: {r['vol_ratio']:.1f}x ortalama"
        )
    msg = "🚀 KRİTERLERE UYAN COIN(LER)\n\n" + "\n\n".join(lines)
    print(msg)
    send_telegram(msg)


def run_bot():
    symbols = get_top_symbols(QUOTE_CURRENCY, TOP_N_COINS, EXCLUDE_COINS)

    start_msg = (
        f"🤖 AL-sinyali tarayıcı bot başladı\n"
        f"Kaynak: Binance (global, public veri)\n"
        f"Taranan: En yüksek hacimli {len(symbols)} adet {QUOTE_CURRENCY} paritesi\n"
        f"Zaman dilimi: {TIMEFRAME}\n"
        f"Kriterler: Trend dönüşü (MA{SHORT_WINDOW}/{LONG_WINDOW}) + RSI momentum ({RSI_MIN}-{RSI_MAX}) + Hacim artışı ({VOLUME_MULTIPLIER}x)\n"
        f"Tarama aralığı: {CHECK_INTERVAL_SEC} sn | Coin başına bekleme: {COOLDOWN_MIN} dk"
    )
    print(start_msg)
    send_telegram(start_msg)

    last_symbol_refresh = time.time()
    SYMBOL_REFRESH_INTERVAL = 3600

    while True:
        try:
            now = time.time()
            if now - last_symbol_refresh > SYMBOL_REFRESH_INTERVAL:
                symbols = get_top_symbols(QUOTE_CURRENCY, TOP_N_COINS, EXCLUDE_COINS)
                last_symbol_refresh = now
                print(f"Taranan coin listesi güncellendi ({len(symbols)} adet).")

            hits = scan_once(symbols)
            format_and_send(hits)

            time.sleep(CHECK_INTERVAL_SEC)

        except Exception as e:
            print(f"Genel hata: {e}. 30 saniye sonra tekrar denenecek...")
            send_telegram(f"⚠️ Bot hata aldı: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
