"""
Kripto AL-Sinyali Tarayıcı Bot — ÜST SEVİYE SÜRÜM (Binance TR / BtcTurk)
================================================================================
Binance TR'nin resmi API dokümantasyonu BtcTurk altyapısına yönlendiği için
bu bot ccxt'nin "btcturk" modülüyle çalışır (Binance TR = BtcTurk altyapısı,
aynı borsa, aynı veri).

KRİTERLER (toplam 100 puan, MIN_SCORE altı bildirilmez):
  1) TREND KESİŞİMİ (20p)  -> Kısa MA, Uzun MA'yı SON 3 MUM içinde
                               yukarı kesmiş (sadece anlık değil, yakın
                               geçmişte olanı da yakalar)
  2) ADX / TREND GÜCÜ (15p) -> ADX >= 20, yani gerçek bir trend var,
                               yatay/kararsız piyasa değil
  3) RSI MOMENTUM (15p)    -> RSI 50-75 arası VE son 3 mumun ortalama
                               eğimi pozitif (tek mumdaki gürültüye
                               takılmaz)
  4) MACD KESİŞİMİ (20p)   -> MACD çizgisi sinyal çizgisini SON 3 MUM
                               içinde yukarı kesmiş
  5) HACİM ARTIŞI (15p)    -> Son mum hacmi, ortalamanın X katı üzerinde
  6) BREAKOUT (15p)        -> Fiyat, son 20 mumun en yükseğini kırmış
                               (yeni yerel zirve = klasik "yükselişe
                               geçti" anı)

SAĞLAMLIK:
  - Bir coin için bazı göstergeler (örn. MACD, ADX) hesaplanacak kadar
    geçmiş veri yoksa coin ATILMAZ; sadece o kriterin puanı boş kalır,
    kalan kriterlerle değerlendirilmeye devam eder. Bu, yeni/az işlem
    gören coinlerin "hiç yakalanamaması" sorununu çözer.
  - Coinler paralel (aynı anda birkaç tane) taranır; taramanın sinyal
    anını kaçırma ihtimalini azaltır.
  - Herhangi bir hata (bağlantı, veri, vb.) botu ÇÖKERTMEZ; hatayı
    Telegram'a tam detayıyla (traceback) gönderir ve bir sonraki turda
    otomatik devam eder.

SAT sinyali yoktur. Gerçek para kullanılmaz, otomatik işlem açılmaz.
Bu bir yatırım tavsiyesi değildir; SL/TP önerileri bilgi amaçlıdır.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY      (varsayılan: TRY)
    TIMEFRAME           (varsayılan: 15m)
    SHORT_WINDOW        (varsayılan: 9)
    LONG_WINDOW         (varsayılan: 21)
    CROSS_LOOKBACK      (varsayılan: 3)   -> kesişim kaç mum içinde aranır
    RSI_PERIOD          (varsayılan: 14)
    RSI_MIN             (varsayılan: 50)
    RSI_MAX             (varsayılan: 75)
    ADX_PERIOD          (varsayılan: 14)
    ADX_MIN             (varsayılan: 20)
    VOLUME_MULTIPLIER   (varsayılan: 1.3)
    BREAKOUT_LOOKBACK   (varsayılan: 20)
    ATR_PERIOD          (varsayılan: 14)
    ATR_SL_MULTIPLIER   (varsayılan: 1.5)
    ATR_TP_MULTIPLIER   (varsayılan: 3.0)
    MIN_SCORE           (varsayılan: 55)
    CHECK_INTERVAL_SEC  (varsayılan: 300)
    EXCLUDE_COINS       (varsayılan: "")
    COOLDOWN_MIN        (varsayılan: 240)
    SCAN_WORKERS        (varsayılan: 6)   -> aynı anda kaç coin taransın
"""

import ccxt
import pandas as pd
import time
import os
import csv
import threading
import traceback
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------- AYARLAR ----------------------
QUOTE_CURRENCY = os.getenv("QUOTE_CURRENCY", "TRY")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")
SHORT_WINDOW = int(os.getenv("SHORT_WINDOW", 9))
LONG_WINDOW = int(os.getenv("LONG_WINDOW", 21))
CROSS_LOOKBACK = int(os.getenv("CROSS_LOOKBACK", 3))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
RSI_MIN = float(os.getenv("RSI_MIN", 50))
RSI_MAX = float(os.getenv("RSI_MAX", 75))
ADX_PERIOD = int(os.getenv("ADX_PERIOD", 14))
ADX_MIN = float(os.getenv("ADX_MIN", 20))
VOLUME_MULTIPLIER = float(os.getenv("VOLUME_MULTIPLIER", 1.3))
BREAKOUT_LOOKBACK = int(os.getenv("BREAKOUT_LOOKBACK", 20))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", 1.5))
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", 3.0))
MIN_SCORE = float(os.getenv("MIN_SCORE", 55))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", 300))
EXCLUDE_COINS = set(x.strip().upper() for x in os.getenv("EXCLUDE_COINS", "").split(",") if x.strip())
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", 240))
SCAN_WORKERS = int(os.getenv("SCAN_WORKERS", 6))
CSV_FILE = "signals.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Binance TR = BtcTurk altyapısı (binance.tr/apidocs -> BtcTurk API'sine yönleniyor)
exchange = ccxt.btcturk({"enableRateLimit": True})

last_signal_time = {}
last_signal_lock = threading.Lock()

# Paralel taramada her thread kendi borsa bağlantısını kullanır
# (ccxt'nin rate-limit durumunu thread'ler arası paylaşmamak için)
_thread_local = threading.local()


def get_thread_exchange():
    exch = getattr(_thread_local, "exchange", None)
    if exch is None:
        exch = ccxt.btcturk({"enableRateLimit": True})
        _thread_local.exchange = exch
    return exch


# ==================== TELEGRAM ====================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış]")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        if len(message) > 3800:
            message = message[:3800] + "\n... (kırpıldı)"
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
        writer.writerow([
            now, symbol, f"{price:.6f}", f"{score:.0f}",
            f"{sl:.6f}" if sl is not None else "",
            f"{tp:.6f}" if tp is not None else "",
        ])


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
    exch = get_thread_exchange()
    duration_sec = exch.parse_timeframe(timeframe)
    now_ms = exch.milliseconds()
    since = now_ms - (limit + 5) * duration_sec * 1000

    data = safe_call(exch.fetch_ohlcv, symbol, timeframe=timeframe, since=since, limit=limit)
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


def compute_adx(df, period):
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0, 1e-10)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def find_recent_cross_up(a, b, lookback):
    """Son `lookback` mum içinde a'nın b'yi yukarı kestiği bir an var mı?
    Varsa (True, kaç mum önce) döner; offset=0 -> en son kapanan mumda."""
    n = len(a)
    max_back = min(lookback, n - 1)
    for offset in range(max_back):
        i = n - 1 - offset
        j = i - 1
        if j < 0:
            break
        ai, aj, bi, bj = a.iloc[i], a.iloc[j], b.iloc[i], b.iloc[j]
        if pd.isna(ai) or pd.isna(aj) or pd.isna(bi) or pd.isna(bj):
            continue
        if aj <= bj and ai > bi:
            return True, offset
    return False, None


def is_breakout(df, lookback):
    if len(df) <= lookback + 1:
        return False
    window = df["close"].iloc[-(lookback + 1):-1]
    if window.isna().all():
        return False
    return df["close"].iloc[-1] > window.max()


# ==================== ANALİZ ====================

def evaluate(symbol):
    fetch_limit = max(LONG_WINDOW, BREAKOUT_LOOKBACK, ADX_PERIOD * 2, 40) + CROSS_LOOKBACK + 5
    df = get_ohlcv(symbol, TIMEFRAME, limit=fetch_limit)

    if len(df) < LONG_WINDOW + 2:
        return None

    price = df["close"].iloc[-1]
    score = 0
    details = []

    df["ma_short"] = df["close"].rolling(SHORT_WINDOW).mean()
    df["ma_long"] = df["close"].rolling(LONG_WINDOW).mean()
    trend_ok, trend_off = find_recent_cross_up(df["ma_short"], df["ma_long"], CROSS_LOOKBACK)
    if trend_ok:
        score += 20
        details.append("Trend kesişimi ✅" if trend_off == 0 else f"Trend kesişimi ✅ ({trend_off} mum önce)")

    if len(df) >= ADX_PERIOD * 2:
        adx_val = compute_adx(df, ADX_PERIOD).iloc[-1]
        if not pd.isna(adx_val) and adx_val >= ADX_MIN:
            score += 15
            details.append(f"Trend gücü ✅ (ADX {adx_val:.0f})")

    if len(df) >= RSI_PERIOD + 4:
        rsi_series = compute_rsi(df["close"], RSI_PERIOD)
        curr_rsi = rsi_series.iloc[-1]
        rsi_slope = rsi_series.diff().iloc[-3:].mean()
        if not pd.isna(curr_rsi) and RSI_MIN <= curr_rsi <= RSI_MAX and not pd.isna(rsi_slope) and rsi_slope > 0:
            score += 15
            details.append(f"RSI momentum ✅ ({curr_rsi:.0f})")

    if len(df) >= 35:
        macd_line, signal_line = compute_macd(df["close"])
        macd_ok, macd_off = find_recent_cross_up(macd_line, signal_line, CROSS_LOOKBACK)
        if macd_ok:
            score += 20
            details.append("MACD kesişimi ✅" if macd_off == 0 else f"MACD kesişimi ✅ ({macd_off} mum önce)")

    vol_lookback = min(20, len(df) - 1)
    if vol_lookback >= 5:
        vol_avg = df["volume"].iloc[-(vol_lookback + 1):-1].mean()
        curr_vol = df["volume"].iloc[-1]
        if vol_avg and vol_avg > 0:
            vol_ratio = curr_vol / vol_avg
            if vol_ratio >= VOLUME_MULTIPLIER:
                score += 15
                details.append(f"Hacim artışı ✅ ({vol_ratio:.1f}x)")

    if is_breakout(df, BREAKOUT_LOOKBACK):
        score += 15
        details.append(f"Yeni yerel zirve ✅ (son {BREAKOUT_LOOKBACK} mum)")
    elif len(df) >= 12 and is_breakout(df, min(10, len(df) - 2)):
        score += 8
        details.append("Kısa vadeli zirve ✅")

    score = min(score, 100)
    if score < MIN_SCORE or not details:
        return None

    stop_loss = take_profit = None
    if len(df) >= ATR_PERIOD + 2:
        atr_val = compute_atr(df, ATR_PERIOD).iloc[-1]
        if not pd.isna(atr_val):
            stop_loss = price - atr_val * ATR_SL_MULTIPLIER
            take_profit = price + atr_val * ATR_TP_MULTIPLIER

    return {
        "price": price,
        "score": score,
        "details": details,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def get_market_trend():
    try:
        df = get_ohlcv(f"BTC/{QUOTE_CURRENCY}", TIMEFRAME, limit=LONG_WINDOW + 5)
        ma_short = df["close"].rolling(SHORT_WINDOW).mean().iloc[-1]
        ma_long = df["close"].rolling(LONG_WINDOW).mean().iloc[-1]
        return "yukarı" if ma_short > ma_long else "aşağı"
    except Exception:
        return "bilinmiyor"


def scan_once(symbols):
    now = time.time()
    to_check = []
    with last_signal_lock:
        for s in symbols:
            if now - last_signal_time.get(s, 0) >= COOLDOWN_MIN * 60:
                to_check.append(s)

    hits = []
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        future_map = {executor.submit(evaluate, s): s for s in to_check}
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"{symbol} taranırken hata: {e}")
                continue
            if result:
                hits.append((symbol, result))
                with last_signal_lock:
                    last_signal_time[symbol] = now
                log_signal(symbol, result["price"], result["score"], result["stop_loss"], result["take_profit"])

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
        sltp = ""
        if r["stop_loss"] is not None and r["take_profit"] is not None:
            sltp = f"\n🛑 SL: {r['stop_loss']:.2f}  🎯 TP: {r['take_profit']:.2f}"
        lines.append(
            f"🟢 <b>{name}</b>  —  Skor: {r['score']:.0f}/100\n"
            f"Fiyat: {r['price']:.2f} {QUOTE_CURRENCY}\n"
            f"{detail_text}{sltp}"
        )
    msg = f"🚀 <b>Binance TR — Yükselişe Geçen Coinler</b>{market_note}\n\n" + "\n\n".join(lines)
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
        f"🤖 <b>Üst seviye AL-sinyali botu başladı</b>\n"
        f"Kaynak: Binance TR (BtcTurk altyapısı)\n"
        f"Taranan: {len(symbols)} adet {QUOTE_CURRENCY} paritesi (paralel, {SCAN_WORKERS} işçi)\n"
        f"Zaman dilimi: {TIMEFRAME}\n"
        f"Kriterler: Trend + ADX + RSI + MACD + Hacim + Breakout (6 katman)\n"
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

            market_trend = get_market_trend()
            hits = scan_once(symbols)
            format_and_send(hits, market_trend)

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
