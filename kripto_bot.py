"""
Kripto AL-Sinyali Tarayıcı Bot — GELİŞMİŞ SÜRÜM
================================================================
Binance (global, ccxt) üzerinden en yüksek hacimli USDT
paritelerini tarar ve çok katmanlı bir analiz uygular.

KRİTERLER (her biri puan katkısı yapar, toplam 100 üzerinden skor):
  1) TREND (25p)     -> Kısa MA, Uzun MA'yı yukarı kesmiş
  2) MOMENTUM (20p)  -> RSI 50-75 arası ve yükseliyor
  3) HACİM (20p)     -> Son hacim, ortalamanın X katı üzerinde
  4) MACD (20p)      -> MACD çizgisi sinyal çizgisini yukarı kesmiş
  5) ÜST ZAMAN DİLİMİ (15p) -> 1 saatlik grafikte de trend yukarı yönlü
                                (yanlış sinyalleri elemek için)

MIN_SCORE altında kalan coinler bildirilmez. Varsayılan eşik 70 —
yani en az 3-4 kriterin birden sağlanması gerekir.

Genel piyasa filtresi: BTC kendisi net düşüşteyse (kısa MA < uzun MA),
bu bilgi mesaja "⚠️ Piyasa geneli zayıf" notuyla eklenir — sinyal
yine de gönderilir ama dikkat notuyla.

Her sinyalde ayrıca ATR (Average True Range) tabanlı öneri stop-loss
ve take-profit seviyeleri de hesaplanıp mesaja eklenir (bilgi amaçlı,
otomatik emir açılmaz).

SAT sinyali yoktur. Gerçek para kullanılmaz, otomatik işlem açılmaz.
Bu bir yatırım tavsiyesi değildir.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY      (varsayılan: USDT)
    TOP_N_COINS         (varsayılan: 100)
    TIMEFRAME           (varsayılan: 15m)
    HIGHER_TIMEFRAME    (varsayılan: 1h)   -> üst zaman dilimi onayı için
    SHORT_WINDOW        (varsayılan: 9)
    LONG_WINDOW         (varsayılan: 21)
    RSI_PERIOD          (varsayılan: 14)
    RSI_MIN             (varsayılan: 50)
    RSI_MAX             (varsayılan: 75)
    VOLUME_MULTIPLIER   (varsayılan: 1.5)
    ATR_PERIOD          (varsayılan: 14)
    ATR_SL_MULTIPLIER   (varsayılan: 1.5)  -> stop-loss = fiyat - ATR*bu
    ATR_TP_MULTIPLIER   (varsayılan: 3.0)  -> take-profit = fiyat + ATR*bu
    MIN_SCORE           (varsayılan: 70)   -> bu puanın altındaki sinyaller atılmaz
    CHECK_INTERVAL_SEC  (varsayılan: 300)
    EXCLUDE_COINS       (varsayılan: "")
    COOLDOWN_MIN        (varsayılan: 240)
"""

import ccxt
import pandas as pd
import time
import os
import csv
import requests
from datetime import datetime

# ---------------------- AYARLAR ----------------------
QUOTE_CURRENCY = os.getenv("QUOTE_CURRENCY", "USDT")
TOP_N_COINS = int(os.getenv("TOP_N_COINS", 100))
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
HIGHER_TIMEFRAME = os.getenv("HIGHER_TIMEFRAME", "1h")
SHORT_WINDOW = int(os.getenv("SHORT_WINDOW", 9))
LONG_WINDOW = int(os.getenv("LONG_WINDOW", 21))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
RSI_MIN = float(os.getenv("RSI_MIN", 50))
RSI_MAX = float(os.getenv("RSI_MAX", 75))
VOLUME_MULTIPLIER = float(os.getenv("VOLUME_MULTIPLIER", 1.5))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", 1.5))
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", 3.0))
MIN_SCORE = float(os.getenv("MIN_SCORE", 70))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 300))
EXCLUDE_COINS = set(x.strip().upper() for x in os.getenv("EXCLUDE_COINS", "").split(",") if x.strip())
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", 240))
CSV_FILE = "signals.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

exchange = ccxt.binance()
last_signal_time = {}


# ==================== TELEGRAM ====================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")


def log_signal(symbol, price, score, sl, tp):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["zaman", "coin", "fiyat", "skor", "stop_loss", "take_profit"])
        writer.writerow([now, symbol, f"{price:.6f}", f"{score:.0f}", f"{sl:.6f}", f"{tp:.6f}"])


# ==================== VERİ ÇEKME (yeniden deneme destekli) ====================

def safe_call(func, *args, retries=3, delay=2, **kwargs):
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"Geçici hata ({func.__name__}): {e}. Tekrar deneniyor ({attempt+1}/{retries})...")
            time.sleep(delay)


def get_top_symbols(quote, top_n, exclude):
    markets = safe_call(exchange.load_markets)
    tickers = safe_call(exchange.fetch_tickers)

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
    data = safe_call(exchange.fetch_ohlcv, symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    return df


# ==================== GÖSTERGELER ====================

def compute_rsi(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def compute_atr(df, period):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def higher_timeframe_bullish(symbol):
    """1 saatlik grafikte kısa MA uzun MA'nın üzerinde mi (genel trend onayı)."""
    try:
        df = get_ohlcv(symbol, HIGHER_TIMEFRAME, limit=LONG_WINDOW + 5)
        if len(df) < LONG_WINDOW:
            return False
        ma_short = df["close"].rolling(SHORT_WINDOW).mean().iloc[-1]
        ma_long = df["close"].rolling(LONG_WINDOW).mean().iloc[-1]
        return ma_short > ma_long
    except Exception:
        return False


def get_market_trend():
    """BTC'nin genel trendi yukarı mı aşağı mı (piyasa notu için)."""
    try:
        df = get_ohlcv(f"BTC/{QUOTE_CURRENCY}", TIMEFRAME, limit=LONG_WINDOW + 5)
        ma_short = df["close"].rolling(SHORT_WINDOW).mean().iloc[-1]
        ma_long = df["close"].rolling(LONG_WINDOW).mean().iloc[-1]
        return "yukarı" if ma_short > ma_long else "aşağı"
    except Exception:
        return "bilinmiyor"


# ==================== ANALİZ ====================

def evaluate(symbol):
    min_needed = max(LONG_WINDOW, RSI_PERIOD, 26) + 20
    df = get_ohlcv(symbol, TIMEFRAME, limit=min_needed)
    if len(df) < min_needed:
        return None

    df["ma_short"] = df["close"].rolling(SHORT_WINDOW).mean()
    df["ma_long"] = df["close"].rolling(LONG_WINDOW).mean()
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)
    df["vol_avg"] = df["volume"].rolling(20).mean()
    df["atr"] = compute_atr(df, ATR_PERIOD)
    macd_line, signal_line = compute_macd(df["close"])
    df["macd"] = macd_line
    df["macd_signal"] = signal_line

    prev_short, prev_long = df["ma_short"].iloc[-2], df["ma_long"].iloc[-2]
    curr_short, curr_long = df["ma_short"].iloc[-1], df["ma_long"].iloc[-1]
    curr_rsi, prev_rsi = df["rsi"].iloc[-1], df["rsi"].iloc[-2]
    curr_vol, avg_vol = df["volume"].iloc[-1], df["vol_avg"].iloc[-1]
    curr_macd, prev_macd = df["macd"].iloc[-1], df["macd"].iloc[-2]
    curr_macd_sig, prev_macd_sig = df["macd_signal"].iloc[-1], df["macd_signal"].iloc[-2]
    curr_atr = df["atr"].iloc[-1]
    price = df["close"].iloc[-1]

    if pd.isna(curr_rsi) or pd.isna(avg_vol) or avg_vol == 0 or pd.isna(curr_atr):
        return None

    # --- Kriterler ve puanlama ---
    score = 0
    details = []

    trend_ok = prev_short <= prev_long and curr_short > curr_long
    if trend_ok:
        score += 25
        details.append("Trend dönüşü ✅")

    momentum_ok = RSI_MIN <= curr_rsi <= RSI_MAX and curr_rsi > prev_rsi
    if momentum_ok:
        score += 20
        details.append(f"RSI momentum ✅ ({curr_rsi:.0f})")

    vol_ratio = curr_vol / avg_vol
    volume_ok = vol_ratio >= VOLUME_MULTIPLIER
    if volume_ok:
        score += 20
        details.append(f"Hacim artışı ✅ ({vol_ratio:.1f}x)")

    macd_ok = prev_macd <= prev_macd_sig and curr_macd > curr_macd_sig
    if macd_ok:
        score += 20
        details.append("MACD kesişimi ✅")

    htf_ok = higher_timeframe_bullish(symbol)
    if htf_ok:
        score += 15
        details.append(f"{HIGHER_TIMEFRAME} trend onayı ✅")

    if score < MIN_SCORE:
        return None

    stop_loss = price - (curr_atr * ATR_SL_MULTIPLIER)
    take_profit = price + (curr_atr * ATR_TP_MULTIPLIER)

    return {
        "price": price,
        "score": score,
        "details": details,
        "rsi": curr_rsi,
        "vol_ratio": vol_ratio,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


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
                log_signal(symbol, result["price"], result["score"], result["stop_loss"], result["take_profit"])

        except Exception as e:
            print(f"{symbol} taranırken hata: {e}")

    hits.sort(key=lambda x: x[1]["score"], reverse=True)
    return hits


def format_and_send(hits, market_trend):
    if not hits:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Kriterlere uyan coin yok. (Piyasa: {market_trend})")
        return

    market_note = ""
    if market_trend == "aşağı":
        market_note = "\n⚠️ <i>Genel piyasa (BTC) şu an düşüş eğiliminde — dikkatli olun</i>\n"

    lines = []
    for symbol, r in hits:
        name = symbol.replace(f"/{QUOTE_CURRENCY}", "")
        detail_text = " · ".join(r["details"])
        lines.append(
            f"🟢 <b>{name}</b>  —  Skor: {r['score']:.0f}/100\n"
            f"Fiyat: {r['price']:.4f} {QUOTE_CURRENCY}\n"
            f"{detail_text}\n"
            f"🛑 SL: {r['stop_loss']:.4f}  🎯 TP: {r['take_profit']:.4f}"
        )
    msg = f"🚀 <b>YÜKSEK SKORLU AL SİNYALLERİ</b>{market_note}\n\n" + "\n\n".join(lines)
    print(msg)
    send_telegram(msg)


# ==================== ANA DÖNGÜ ====================

def run_bot():
    symbols = get_top_symbols(QUOTE_CURRENCY, TOP_N_COINS, EXCLUDE_COINS)

    start_msg = (
        f"🤖 <b>Gelişmiş AL-sinyali botu başladı</b>\n"
        f"Kaynak: Binance (global) | Taranan: {len(symbols)} adet {QUOTE_CURRENCY} paritesi\n"
        f"Zaman dilimi: {TIMEFRAME} (üst onay: {HIGHER_TIMEFRAME})\n"
        f"Kriterler: Trend + RSI + Hacim + MACD + Üst-TF onayı\n"
        f"Minimum skor: {MIN_SCORE}/100\n"
        f"Tarama aralığı: {CHECK_INTERVAL_SEC} sn | Coin başına bekleme: {COOLDOWN_MIN} dk\n"
        f"Her sinyalde ATR tabanlı SL/TP önerisi de gelir."
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

            market_trend = get_market_trend()
            hits = scan_once(symbols)
            format_and_send(hits, market_trend)

            time.sleep(CHECK_INTERVAL_SEC)

        except Exception as e:
            print(f"Genel hata: {e}. 30 saniye sonra tekrar denenecek...")
            try:
                send_telegram(f"⚠️ Bot geçici hata aldı, otomatik devam ediyor: {e}")
            except Exception:
                pass
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
