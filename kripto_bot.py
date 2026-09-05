"""
Kripto AL-Sinyali Tarayıcı Bot — Binance TR (BtcTurk altyapısı)
================================================================
ÖNEMLİ TEKNİK NOT: Binance TR'nin resmi API dokümantasyonu
(binance.tr/apidocs), doğrudan BtcTurk'ün API'sine yönlenir.
Binance TR, teknik olarak BtcTurk borsa altyapısını kullanır.
Bu yüzden bu bot ccxt'nin "btcturk" borsa modülü ile çalışır —
bu, Binance TR'ye bağlanmanın doğru ve tek yoludur.

Bu bot, TRY paritelerini tarar ve üç ana katmanla puanlar:
  1) TREND (40p)   -> Kısa MA, Uzun MA'yı yukarı kesmiş
  2) MOMENTUM (30p) -> RSI 50-75 arası ve yükseliyor
  3) HACİM (30p)   -> Son hacim, ortalamanın X katı üzerinde

MIN_SCORE altında kalanlar bildirilmez.

SAĞLAMLIK: Herhangi bir hata oluşursa (bağlantı, sembol, veri
sorunu vb.) bot ÇÖKMEZ; hatayı konsola yazar, Telegram'a kısa bir
özet + tam hata detayını gönderir ve bir sonraki turda tekrar dener.

SAT sinyali yoktur. Gerçek para kullanılmaz, otomatik işlem açılmaz.
Bu bir yatırım tavsiyesi değildir.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY      (varsayılan: TRY)
    TIMEFRAME           (varsayılan: 15m)
    SHORT_WINDOW        (varsayılan: 9)
    LONG_WINDOW         (varsayılan: 21)
    RSI_PERIOD          (varsayılan: 14)
    RSI_MIN             (varsayılan: 50)
    RSI_MAX             (varsayılan: 75)
    VOLUME_MULTIPLIER   (varsayılan: 1.3)
    MIN_SCORE           (varsayılan: 60)
    CHECK_INTERVAL_SEC  (varsayılan: 300)
    EXCLUDE_COINS       (varsayılan: "")
    COOLDOWN_MIN        (varsayılan: 240)
"""

import ccxt
import pandas as pd
import time
import os
import csv
import traceback
import requests
from datetime import datetime

# ---------------------- AYARLAR ----------------------
QUOTE_CURRENCY = os.getenv("QUOTE_CURRENCY", "TRY")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
SHORT_WINDOW = int(os.getenv("SHORT_WINDOW", 9))
LONG_WINDOW = int(os.getenv("LONG_WINDOW", 21))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
RSI_MIN = float(os.getenv("RSI_MIN", 50))
RSI_MAX = float(os.getenv("RSI_MAX", 75))
VOLUME_MULTIPLIER = float(os.getenv("VOLUME_MULTIPLIER", 1.3))
MIN_SCORE = float(os.getenv("MIN_SCORE", 60))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 300))
EXCLUDE_COINS = set(x.strip().upper() for x in os.getenv("EXCLUDE_COINS", "").split(",") if x.strip())
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", 240))
CSV_FILE = "signals.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Binance TR = BtcTurk altyapısı (binance.tr/apidocs -> BtcTurk API'sine yönleniyor)
exchange = ccxt.btcturk()

last_signal_time = {}


# ==================== TELEGRAM ====================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        # Telegram mesaj limiti ~4096 karakter, uzun hataları kırp
        if len(message) > 3800:
            message = message[:3800] + "\n... (kırpıldı)"
        requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram gönderim hatası: {e}")


def log_signal(symbol, price, score):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["zaman", "coin", "fiyat", "skor"])
        writer.writerow([now, symbol, f"{price:.6f}", f"{score:.0f}"])


# ==================== VERİ ÇEKME (yeniden deneme destekli) ====================

def safe_call(func, *args, retries=3, delay=3, **kwargs):
    last_err = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            print(f"Geçici hata ({getattr(func, '__name__', 'call')}): {e}. Tekrar deneniyor ({attempt+1}/{retries})...")
            time.sleep(delay)
    raise last_err


def get_symbols(quote, exclude):
    """BtcTurk'te (Binance TR) verilen kur cinsinden aktif işlem gören tüm pariteleri döndürür."""
    markets = safe_call(exchange.load_markets)
    symbols = []
    for symbol, market in markets.items():
        try:
            if market.get("quote") != quote:
                continue
            base = market.get("base", "")
            if base in exclude:
                continue
            if market.get("active") is False:
                continue
            symbols.append(symbol)
        except Exception:
            continue
    return symbols


def get_ohlcv(symbol, timeframe, limit):
    # BtcTurk (Binance TR) kline API'si zaman aralığını 'from'/'to' (unix
    # saniye) ile bekliyor. ccxt bunu 'since' üzerinden ayarlıyor; since
    # verilmezse istek eksik kalıp INVALID_REQUEST_ERROR dönüyor. Bu yüzden
    # kaç mum istediğimize göre 'since' değerini kendimiz hesaplıyoruz.
    duration_sec = exchange.parse_timeframe(timeframe)  # örn. 15m -> 900
    now_ms = exchange.milliseconds()
    since = now_ms - (limit + 5) * duration_sec * 1000  # birkaç mumluk pay bırak

    data = safe_call(exchange.fetch_ohlcv, symbol, timeframe=timeframe, since=since, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if len(df) > limit:
        df = df.iloc[-limit:].reset_index(drop=True)
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


# ==================== ANALİZ ====================

def evaluate(symbol):
    min_needed = max(LONG_WINDOW, RSI_PERIOD) + 20
    df = get_ohlcv(symbol, TIMEFRAME, limit=min_needed)
    if len(df) < min_needed:
        return None

    df["ma_short"] = df["close"].rolling(SHORT_WINDOW).mean()
    df["ma_long"] = df["close"].rolling(LONG_WINDOW).mean()
    df["rsi"] = compute_rsi(df["close"], RSI_PERIOD)
    df["vol_avg"] = df["volume"].rolling(20).mean()

    prev_short, prev_long = df["ma_short"].iloc[-2], df["ma_long"].iloc[-2]
    curr_short, curr_long = df["ma_short"].iloc[-1], df["ma_long"].iloc[-1]
    curr_rsi, prev_rsi = df["rsi"].iloc[-1], df["rsi"].iloc[-2]
    curr_vol, avg_vol = df["volume"].iloc[-1], df["vol_avg"].iloc[-1]
    price = df["close"].iloc[-1]

    if pd.isna(curr_rsi) or pd.isna(avg_vol) or avg_vol == 0:
        return None

    score = 0
    details = []

    trend_ok = prev_short <= prev_long and curr_short > curr_long
    if trend_ok:
        score += 40
        details.append("Trend dönüşü ✅")

    momentum_ok = RSI_MIN <= curr_rsi <= RSI_MAX and curr_rsi > prev_rsi
    if momentum_ok:
        score += 30
        details.append(f"RSI momentum ✅ ({curr_rsi:.0f})")

    vol_ratio = curr_vol / avg_vol
    volume_ok = vol_ratio >= VOLUME_MULTIPLIER
    if volume_ok:
        score += 30
        details.append(f"Hacim artışı ✅ ({vol_ratio:.1f}x)")

    if score < MIN_SCORE:
        return None

    return {"price": price, "score": score, "details": details, "rsi": curr_rsi, "vol_ratio": vol_ratio}


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
                log_signal(symbol, result["price"], result["score"])

        except Exception as e:
            print(f"{symbol} taranırken hata: {e}")

    hits.sort(key=lambda x: x[1]["score"], reverse=True)
    return hits


def format_and_send(hits):
    if not hits:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Kriterlere uyan coin yok.")
        return

    lines = []
    for symbol, r in hits:
        name = symbol.replace(f"/{QUOTE_CURRENCY}", "")
        detail_text = " · ".join(r["details"])
        lines.append(
            f"🟢 <b>{name}</b>  —  Skor: {r['score']:.0f}/100\n"
            f"Fiyat: {r['price']:.2f} {QUOTE_CURRENCY}\n"
            f"{detail_text}"
        )
    msg = "🚀 <b>Binance TR — Yükselişe Geçen Coinler</b>\n\n" + "\n\n".join(lines)
    print(msg)
    send_telegram(msg)


# ==================== ANA DÖNGÜ ====================

def run_bot():
    try:
        symbols = get_symbols(QUOTE_CURRENCY, EXCLUDE_COINS)
    except Exception as e:
        error_detail = traceback.format_exc()
        print(error_detail)
        send_telegram(f"❌ Bot başlarken hata aldı (coin listesi çekilemedi):\n\n{e}\n\n{error_detail}")
        raise

    start_msg = (
        f"🤖 <b>Binance TR AL-sinyali botu başladı</b>\n"
        f"Kaynak: Binance TR (BtcTurk altyapısı)\n"
        f"Taranan: {len(symbols)} adet {QUOTE_CURRENCY} paritesi\n"
        f"Zaman dilimi: {TIMEFRAME}\n"
        f"Kriterler: Trend + RSI momentum + Hacim artışı\n"
        f"Minimum skor: {MIN_SCORE}/100\n"
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
                symbols = get_symbols(QUOTE_CURRENCY, EXCLUDE_COINS)
                last_symbol_refresh = now
                print(f"Taranan coin listesi güncellendi ({len(symbols)} adet).")

            hits = scan_once(symbols)
            format_and_send(hits)

            time.sleep(CHECK_INTERVAL_SEC)

        except Exception as e:
            error_detail = traceback.format_exc()
            print(error_detail)
            try:
                send_telegram(f"⚠️ Bot hata aldı, 30 sn sonra tekrar denenecek:\n\n{e}\n\n{error_detail}")
            except Exception:
                pass
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
    
