"""
Kripto AL-Sinyali Tarayıcı Bot — ÜST SEVİYE + SETUP KARTI + ANOMALİ TESPİTİ
================================================================================
Binance TR (BtcTurk altyapısı) üzerinden çalışır. İki ayrı bildirim türü var:

1) SETUP SİNYALİ — çok kriterli, "gerçek trend" onaylı fırsatlar (aşağıdaki
   6 kriterin puanlı toplamı MIN_SCORE'u geçerse gönderilir):
     TREND KESİŞİMİ (20p) · ADX/Trend gücü (15p) · RSI momentum (15p) ·
     MACD kesişimi (20p) · Hacim artışı (15p) · Breakout (15p)
   Mesaj, "setup kartı" formatında gelir: fiyat, SL/TP, risk/ödül oranı,
   güven seviyesi (🟢/🟢🟢/🟢🟢🟢) ve hangi kriterlerin sağlandığı.

2) ANOMALİ UYARISI — setup kriterlerinden bağımsız, ayrı bir tespit:
   kısa sürede ani %X fiyat sıçraması + hacim patlaması. Bu bir trend
   onayı DEĞİLDİR, sadece "burada olağandışı bir hareket var" bildirimidir.
   Pump hareketleri sık sık hızla geri çekildiği için mesajda her zaman
   açık bir risk notu bulunur.

SAĞLAMLIK: Bir coin için bazı göstergeler (MACD, ADX gibi) hesaplanacak
kadar veri yoksa coin ATILMAZ, sadece o kriterin puanı boş kalır. Coinler
paralel taranır. Herhangi bir hata botu çökertmez, Telegram'a tam hata
detayıyla (traceback) düşer ve bot otomatik devam eder.

SAT sinyali yoktur. GERÇEK PARA KULLANILMAZ, otomatik işlem açılmaz.
Bu bir yatırım tavsiyesi değildir; SL/TP ve anomali bilgileri sadece
bilgilendirme amaçlıdır.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY       (varsayılan: TRY)
    TIMEFRAME            (varsayılan: 15m)
    SHORT_WINDOW         (varsayılan: 9)
    LONG_WINDOW          (varsayılan: 21)
    CROSS_LOOKBACK       (varsayılan: 3)
    RSI_PERIOD           (varsayılan: 14)
    RSI_MIN              (varsayılan: 50)
    RSI_MAX              (varsayılan: 75)
    ADX_PERIOD           (varsayılan: 14)
    ADX_MIN              (varsayılan: 20)
    VOLUME_MULTIPLIER    (varsayılan: 1.3)
    BREAKOUT_LOOKBACK    (varsayılan: 20)
    ATR_PERIOD           (varsayılan: 14)
    ATR_SL_MULTIPLIER    (varsayılan: 1.5)
    ATR_TP_MULTIPLIER    (varsayılan: 3.0)
    MIN_SCORE            (varsayılan: 55)
    CHECK_INTERVAL_SEC   (varsayılan: 300)
    EXCLUDE_COINS        (varsayılan: "")
    COOLDOWN_MIN         (varsayılan: 240)   -> setup sinyali için coin başına bekleme
    SCAN_WORKERS         (varsayılan: 6)
    ANOMALY_PCT_THRESHOLD (varsayılan: 5.0)  -> % kaç ani sıçrama anomali sayılsın
    ANOMALY_VOLUME_MULT  (varsayılan: 2.5)   -> hacim ortalamanın kaç katı olmalı
    ANOMALY_WINDOW       (varsayılan: 3)     -> kaç mumluk pencerede bakılsın
    ANOMALY_COOLDOWN_MIN (varsayılan: 60)    -> anomali için coin başına bekleme
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

ANOMALY_PCT_THRESHOLD = float(os.getenv("ANOMALY_PCT_THRESHOLD", 5.0))
ANOMALY_VOLUME_MULT = float(os.getenv("ANOMALY_VOLUME_MULT", 2.5))
ANOMALY_WINDOW = int(os.getenv("ANOMALY_WINDOW", 3))
ANOMALY_COOLDOWN_MIN = int(os.getenv("ANOMALY_COOLDOWN_MIN", 60))

CSV_FILE = "signals.csv"
ANOMALY_CSV_FILE = "anomalies.csv"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Binance TR = BtcTurk altyapısı (binance.tr/apidocs -> BtcTurk API'sine yönleniyor)
exchange = ccxt.btcturk({"enableRateLimit": True})

last_signal_time = {}
last_signal_lock = threading.Lock()
last_anomaly_time = {}
last_anomaly_lock = threading.Lock()

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


def log_signal(symbol, r):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["zaman", "coin", "fiyat", "skor", "stop_loss", "take_profit"])
        writer.writerow([
            now, symbol, f"{r['price']:.6f}", f"{r['score']:.0f}",
            f"{r['stop_loss']:.6f}" if r["stop_loss"] is not None else "",
            f"{r['take_profit']:.6f}" if r["take_profit"] is not None else "",
        ])


def log_anomaly(symbol, a):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_exists = os.path.isfile(ANOMALY_CSV_FILE)
    with open(ANOMALY_CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["zaman", "coin", "fiyat", "yuzde_degisim", "hacim_orani"])
        writer.writerow([now, symbol, f"{a['price']:.6f}", f"{a['pct_change']:.2f}", f"{a['vol_ratio']:.2f}"])


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


def detect_anomaly(df, threshold_pct, vol_mult, window):
    """Son `window` mumda ani fiyat sıçraması + hacim patlaması var mı?
    Bu bir trend onayı değildir, sadece olağandışı hareket tespitidir."""
    if len(df) < window + 21:
        return None
    price_now = df["close"].iloc[-1]
    price_before = df["close"].iloc[-1 - window]
    if price_before <= 0:
        return None
    pct_change = (price_now - price_before) / price_before * 100

    vol_avg = df["volume"].iloc[-21:-1].mean()
    vol_now = df["volume"].iloc[-1]
    if not vol_avg or vol_avg <= 0:
        return None
    vol_ratio = vol_now / vol_avg

    if pct_change >= threshold_pct and vol_ratio >= vol_mult:
        return {"pct_change": pct_change, "vol_ratio": vol_ratio, "price": price_now}
    return None


def confidence_label(score):
    if score >= 80:
        return "🟢🟢🟢 Güçlü"
    elif score >= 65:
        return "🟢🟢 Orta-Güçlü"
    return "🟢 Standart"


# ==================== ANALİZ ====================

def evaluate(symbol):
    fetch_limit = max(LONG_WINDOW, BREAKOUT_LOOKBACK, ADX_PERIOD * 2, 40) + CROSS_LOOKBACK + 5
    df = get_ohlcv(symbol, TIMEFRAME, limit=fetch_limit)

    if len(df) < LONG_WINDOW + 2:
        return None

    result = {"setup": None, "anomaly": None}
    price = df["close"].iloc[-1]
    score = 0
    details = []

    # --- 1) Trend kesişimi ---
    df["ma_short"] = df["close"].rolling(SHORT_WINDOW).mean()
    df["ma_long"] = df["close"].rolling(LONG_WINDOW).mean()
    trend_ok, trend_off = find_recent_cross_up(df["ma_short"], df["ma_long"], CROSS_LOOKBACK)
    if trend_ok:
        score += 20
        details.append("Trend kesişimi ✅" if trend_off == 0 else f"Trend kesişimi ✅ ({trend_off} mum önce)")

    # --- 2) ADX / trend gücü ---
    if len(df) >= ADX_PERIOD * 2:
        adx_val = compute_adx(df, ADX_PERIOD).iloc[-1]
        if not pd.isna(adx_val) and adx_val >= ADX_MIN:
            score += 15
            details.append(f"Trend gücü ✅ (ADX {adx_val:.0f})")

    # --- 3) RSI momentum ---
    if len(df) >= RSI_PERIOD + 4:
        rsi_series = compute_rsi(df["close"], RSI_PERIOD)
        curr_rsi = rsi_series.iloc[-1]
        rsi_slope = rsi_series.diff().iloc[-3:].mean()
        if not pd.isna(curr_rsi) and RSI_MIN <= curr_rsi <= RSI_MAX and not pd.isna(rsi_slope) and rsi_slope > 0:
            score += 15
            details.append(f"RSI momentum ✅ ({curr_rsi:.0f})")

    # --- 4) MACD kesişimi ---
    if len(df) >= 35:
        macd_line, signal_line = compute_macd(df["close"])
        macd_ok, macd_off = find_recent_cross_up(macd_line, signal_line, CROSS_LOOKBACK)
        if macd_ok:
            score += 20
            details.append("MACD kesişimi ✅" if macd_off == 0 else f"MACD kesişimi ✅ ({macd_off} mum önce)")

    # --- 5) Hacim artışı ---
    vol_lookback = min(20, len(df) - 1)
    if vol_lookback >= 5:
        vol_avg = df["volume"].iloc[-(vol_lookback + 1):-1].mean()
        curr_vol = df["volume"].iloc[-1]
        if vol_avg and vol_avg > 0:
            vol_ratio = curr_vol / vol_avg
            if vol_ratio >= VOLUME_MULTIPLIER:
                score += 15
                details.append(f"Hacim artışı ✅ ({vol_ratio:.1f}x)")

    # --- 6) Breakout ---
    if is_breakout(df, BREAKOUT_LOOKBACK):
        score += 15
        details.append(f"Yeni yerel zirve ✅ (son {BREAKOUT_LOOKBACK} mum)")
    elif len(df) >= 12 and is_breakout(df, min(10, len(df) - 2)):
        score += 8
        details.append("Kısa vadeli zirve ✅")

    score = min(score, 100)
    if score >= MIN_SCORE and details:
        stop_loss = take_profit = None
        if len(df) >= ATR_PERIOD + 2:
            atr_val = compute_atr(df, ATR_PERIOD).iloc[-1]
            if not pd.isna(atr_val):
                stop_loss = price - atr_val * ATR_SL_MULTIPLIER
                take_profit = price + atr_val * ATR_TP_MULTIPLIER

        result["setup"] = {
            "price": price,
            "score": score,
            "details": details,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    # --- Anomali tespiti (setup'tan bağımsız) ---
    anomaly = detect_anomaly(df, ANOMALY_PCT_THRESHOLD, ANOMALY_VOLUME_MULT, ANOMALY_WINDOW)
    if anomaly:
        result["anomaly"] = anomaly

    if result["setup"] is None and result["anomaly"] is None:
        return None
    return result


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
    setup_hits = []
    anomaly_hits = []

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        future_map = {executor.submit(evaluate, s): s for s in symbols}
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                result = future.result()
            except Exception as e:
                print(f"{symbol} taranırken hata: {e}")
                continue
            if not result:
                continue

            if result["setup"]:
                with last_signal_lock:
                    ready = now - last_signal_time.get(symbol, 0) >= COOLDOWN_MIN * 60
                    if ready:
                        last_signal_time[symbol] = now
                if ready:
                    setup_hits.append((symbol, result["setup"]))
                    log_signal(symbol, result["setup"])

            if result["anomaly"]:
                with last_anomaly_lock:
                    a_ready = now - last_anomaly_time.get(symbol, 0) >= ANOMALY_COOLDOWN_MIN * 60
                    if a_ready:
                        last_anomaly_time[symbol] = now
                if a_ready:
                    anomaly_hits.append((symbol, result["anomaly"]))
                    log_anomaly(symbol, result["anomaly"])

    setup_hits.sort(key=lambda x: x[1]["score"], reverse=True)
    anomaly_hits.sort(key=lambda x: x[1]["pct_change"], reverse=True)
    return setup_hits, anomaly_hits


# ==================== MESAJ KARTLARI ====================

def build_setup_card(symbol, r):
    name = symbol.replace(f"/{QUOTE_CURRENCY}", "")
    conf = confidence_label(r["score"])
    lines = [
        f"🎯 <b>SETUP: {name}/{QUOTE_CURRENCY}</b>",
        "━━━━━━━━━━━━━━",
        f"Güven: {conf} ({r['score']:.0f}/100)",
        "",
        f"💰 Fiyat: {r['price']:.2f} {QUOTE_CURRENCY}",
    ]
    if r["stop_loss"] is not None and r["take_profit"] is not None and r["price"] > 0:
        sl_pct = (r["stop_loss"] - r["price"]) / r["price"] * 100
        tp_pct = (r["take_profit"] - r["price"]) / r["price"] * 100
        risk = r["price"] - r["stop_loss"]
        reward = r["take_profit"] - r["price"]
        lines.append(f"🛑 Stop-Loss: {r['stop_loss']:.2f} {QUOTE_CURRENCY} ({sl_pct:.1f}%)")
        lines.append(f"🎯 Take-Profit: {r['take_profit']:.2f} {QUOTE_CURRENCY} ({tp_pct:+.1f}%)")
        if risk > 0:
            lines.append(f"⚖️ Risk/Ödül: 1:{reward / risk:.1f}")
    lines.append("")
    lines.append("✅ Kriterler:")
    for d in r["details"]:
        lines.append(f"• {d}")
    lines.append("━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_anomaly_card(symbol, a):
    name = symbol.replace(f"/{QUOTE_CURRENCY}", "")
    lines = [
        f"⚡ <b>ANOMALİ: {name}/{QUOTE_CURRENCY}</b>",
        "━━━━━━━━━━━━━━",
        f"Ani hareket: {a['pct_change']:+.1f}%  |  Hacim: {a['vol_ratio']:.1f}x ortalama",
        f"Fiyat: {a['price']:.2f} {QUOTE_CURRENCY}",
        "",
        "⚠️ Bu tür ani hareketler çoğu zaman hızla geri çekilir (pump & dump "
        "riski). Bu bir trend onayı DEĞİLDİR, sadece olağandışı hareket bildirimidir.",
        "━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def send_setup_alerts(hits, market_trend):
    if not hits:
        return
    market_note = ""
    if market_trend == "aşağı":
        market_note = "\n⚠️ <i>Genel piyasa (BTC) şu an düşüş eğiliminde — dikkatli olun</i>\n"
    cards = [build_setup_card(symbol, r) for symbol, r in hits]
    msg = f"🚀 <b>Binance TR — Yeni Setup'lar</b>{market_note}\n\n" + "\n\n".join(cards)
    print(msg)
    send_telegram(msg)


def send_anomaly_alerts(hits):
    if not hits:
        return
    cards = [build_anomaly_card(symbol, a) for symbol, a in hits]
    msg = "⚡ <b>Anormal Hareket Tespiti</b>\n\n" + "\n\n".join(cards)
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
        f"Zaman dilimi: {TIMEFRAME}\n\n"
        f"🎯 <b>Setup sinyali:</b> Trend + ADX + RSI + MACD + Hacim + Breakout\n"
        f"Min. skor: {MIN_SCORE}/100 | Güven: 🟢 Standart · 🟢🟢 Orta-Güçlü · 🟢🟢🟢 Güçlü\n\n"
        f"⚡ <b>Anomali uyarısı:</b> {ANOMALY_WINDOW} mumda %{ANOMALY_PCT_THRESHOLD:.0f}+ "
        f"sıçrama + {ANOMALY_VOLUME_MULT:.1f}x hacim (bilgi amaçlı, trend onayı değildir)\n\n"
        f"Tarama aralığı: {CHECK_INTERVAL_SEC} sn"
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
            setup_hits, anomaly_hits = scan_once(symbols)

            send_setup_alerts(setup_hits, market_trend)
            send_anomaly_alerts(anomaly_hits)

            if not setup_hits and not anomaly_hits:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Kriterlere uyan coin yok. (Piyasa: {market_trend})")

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
