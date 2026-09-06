"""
Kripto AL-Sinyali Tarayıcı Bot — PROFESYONEL SÜRÜM (Binance TR / BtcTurk)
================================================================================
Binance TR'nin resmi API dokümantasyonu BtcTurk altyapısına yönlendiği için bu
bot ccxt'nin "btcturk" modülüyle çalışır (Binance TR = BtcTurk altyapısı).

İki bildirim türü:
  1) SETUP SİNYALİ  — Trend + ADX + RSI + MACD + Hacim + Breakout (6 kriter,
     100 üzerinden puan). "Setup kartı" formatında: fiyat, SL/TP, risk/ödül
     oranı, güven seviyesi, sağlanan kriterler.
  2) ANOMALİ UYARISI — kısa sürede ani %X fiyat sıçraması + hacim patlaması.
     Trend onayı DEĞİLDİR, sadece bilgilendirme; mesajda her zaman risk notu
     bulunur (pump & dump riski).

Bu sürümde önceki koda göre düzeltilen gerçek sorunlar:
  - Paylaşımlı rate limiter: önceden her thread kendi ccxt bağlantısını
    kullandığı için "rate limit koruması" thread sayısı kadar gevşiyordu.
    Artık TÜM thread'lerin gördüğü tek bir hız sınırlayıcı var.
  - safe_call artık kalıcı hataları (geçersiz sembol vb.) tekrar denemiyor;
    sadece gerçek ağ hatalarında (ccxt.NetworkError) yeniden dener.
  - Sinyal/anomali geçmişi artık SQLite'a yazılıyor (CSV yerine) ve DATA_DIR
    ortam değişkeniyle bir Railway Volume'una yönlendirilebiliyor — aksi
    halde önceki CSV'ler gibi bu da her deploy'da sıfırlanır.
  - Telegram gönderimi artık yeniden deneniyor ve başarısızlık açıkça
    loglanıyor (önceden sessizce kayboluyordu).
  - print() yerine gerçek bir logging kurulumu (seviye LOG_LEVEL ile
    ayarlanabilir).
  - Ortam değişkenleri yanlış girilirse bot anlaşılır bir hatayla (ve
    mümkünse Telegram bildirimiyle) düzgünce durur, çökmez.
  - SIGTERM/SIGINT yakalanıyor; Railway yeniden başlatırken/durdururken
    Telegram'a haber gidiyor, iş parçacıkları güvenle tamamlanıyor.

SAT sinyali yoktur. GERÇEK PARA KULLANILMAZ, otomatik işlem açılmaz. Bu bir
yatırım tavsiyesi değildir; SL/TP ve anomali bilgileri bilgilendirme
amaçlıdır.

Gerekli ortam değişkenleri:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Kalıcılık için (opsiyonel ama önerilir):
    DATA_DIR  -> Railway'de bir Volume ekleyip mount path'ini buraya ver
                 (örn. "/data"). Verilmezse sinyal geçmişi her deploy'da
                 sıfırlanır (Railway'in dosya sistemi varsayılan olarak
                 geçicidir).

Diğer opsiyonel ortam değişkenleri:
    QUOTE_CURRENCY, TIMEFRAME, SHORT_WINDOW, LONG_WINDOW, CROSS_LOOKBACK,
    RSI_PERIOD, RSI_MIN, RSI_MAX, ADX_PERIOD, ADX_MIN, VOLUME_MULTIPLIER,
    BREAKOUT_LOOKBACK, ATR_PERIOD, ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER,
    MIN_SCORE, CHECK_INTERVAL_SEC, EXCLUDE_COINS, COOLDOWN_MIN,
    SCAN_WORKERS, ANOMALY_PCT_THRESHOLD, ANOMALY_VOLUME_MULT,
    ANOMALY_WINDOW, ANOMALY_COOLDOWN_MIN, MIN_REQUEST_INTERVAL_SEC,
    LOG_LEVEL (varsayılan: INFO)

(Varsayılan değerler için kodun ilerisindeki _get_* çağrılarına bakabilirsin.)
"""

from __future__ import annotations

import ccxt
import pandas as pd
import time
import os
import sys
import sqlite3
import signal
import logging
import threading
import traceback
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed


# ==================== TEMEL ALTYAPI (config, log, telegram) ====================

class ConfigError(Exception):
    """Ortam değişkenlerinden gelen konfigürasyon geçersiz olduğunda fırlatılır."""


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        raise ConfigError(f"{name} tam sayı olmalı, gelen değer: {val!r}")


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        raise ConfigError(f"{name} sayısal olmalı, gelen değer: {val!r}")


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("hacim_radari")

TR_TZ = ZoneInfo("Europe/Istanbul")


def now_tr() -> datetime:
    """Sunucu (Railway) genelde UTC çalışır; mesaj/loglarda kafa karışmaması
    için zamanı her zaman Türkiye saatine çevirerek kullanıyoruz."""
    return datetime.now(TR_TZ)


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message: str, retries: int = 3, delay: float = 2.0) -> bool:
    """Telegram'a mesaj gönderir; geçici hatalarda birkaç kez dener.
    Sonunda yine de gönderilemezse (mesaj kaybolur) bunu açıkça loglar,
    böylece en azından Railway loglarından fark edilebilir."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram devre dışı: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ayarlanmamış")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(message) > 3800:
        message = message[:3800] + "\n... (kırpıldı)"

    for attempt in range(retries):
        try:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.ok:
                return True
            logger.warning("Telegram API hatası (kod %s): %s", resp.status_code, resp.text[:200])
        except requests.RequestException as e:
            logger.warning("Telegram gönderim hatası (deneme %d/%d): %s", attempt + 1, retries, e)
        time.sleep(delay)

    logger.error("Telegram mesajı %d denemede de gönderilemedi, mesaj kayboldu: %s", retries, message[:100])
    return False


# ==================== KONFİGÜRASYON ====================
# Yanlış/eksik bir ortam değişkeni burada net bir ConfigError olarak
# yakalanır; bot anlaşılmaz bir traceback ile değil, açık bir mesajla durur.

try:
    QUOTE_CURRENCY = _get_str("QUOTE_CURRENCY", "TRY")
    TIMEFRAME = _get_str("TIMEFRAME", "15m")
    SHORT_WINDOW = _get_int("SHORT_WINDOW", 9)
    LONG_WINDOW = _get_int("LONG_WINDOW", 21)
    CROSS_LOOKBACK = _get_int("CROSS_LOOKBACK", 3)
    RSI_PERIOD = _get_int("RSI_PERIOD", 14)
    RSI_MIN = _get_float("RSI_MIN", 50)
    RSI_MAX = _get_float("RSI_MAX", 75)
    ADX_PERIOD = _get_int("ADX_PERIOD", 14)
    ADX_MIN = _get_float("ADX_MIN", 20)
    VOLUME_MULTIPLIER = _get_float("VOLUME_MULTIPLIER", 1.3)
    BREAKOUT_LOOKBACK = _get_int("BREAKOUT_LOOKBACK", 20)
    ATR_PERIOD = _get_int("ATR_PERIOD", 14)
    ATR_SL_MULTIPLIER = _get_float("ATR_SL_MULTIPLIER", 1.5)
    ATR_TP_MULTIPLIER = _get_float("ATR_TP_MULTIPLIER", 3.0)
    MIN_SCORE = _get_float("MIN_SCORE", 55)
    CHECK_INTERVAL_SEC = _get_int("CHECK_INTERVAL_SEC", 300)
    EXCLUDE_COINS = set(x.strip().upper() for x in _get_str("EXCLUDE_COINS", "").split(",") if x.strip())
    COOLDOWN_MIN = _get_int("COOLDOWN_MIN", 240)
    SCAN_WORKERS = _get_int("SCAN_WORKERS", 6)

    ANOMALY_PCT_THRESHOLD = _get_float("ANOMALY_PCT_THRESHOLD", 5.0)
    ANOMALY_VOLUME_MULT = _get_float("ANOMALY_VOLUME_MULT", 2.5)
    ANOMALY_WINDOW = _get_int("ANOMALY_WINDOW", 3)
    ANOMALY_COOLDOWN_MIN = _get_int("ANOMALY_COOLDOWN_MIN", 60)

    DATA_DIR = _get_str("DATA_DIR", ".")
    MIN_REQUEST_INTERVAL_SEC = _get_float("MIN_REQUEST_INTERVAL_SEC", -1.0)  # -1 -> borsanın kendi değerini kullan

    if not (0 <= RSI_MIN < RSI_MAX <= 100):
        raise ConfigError(f"RSI_MIN/RSI_MAX geçersiz: {RSI_MIN}/{RSI_MAX} (0 <= RSI_MIN < RSI_MAX <= 100 olmalı)")
    if not (0 <= MIN_SCORE <= 100):
        raise ConfigError(f"MIN_SCORE 0-100 aralığında olmalı: {MIN_SCORE}")
    if SHORT_WINDOW >= LONG_WINDOW:
        raise ConfigError(f"SHORT_WINDOW, LONG_WINDOW'dan küçük olmalı: {SHORT_WINDOW}/{LONG_WINDOW}")
    _VALID_TIMEFRAMES = {"1m", "15m", "30m", "1h", "4h", "1d", "1w"}
    if TIMEFRAME not in _VALID_TIMEFRAMES:
        raise ConfigError(
            f"TIMEFRAME BtcTurk'te desteklenmeyebilir: {TIMEFRAME!r} (bilinenler: {sorted(_VALID_TIMEFRAMES)})"
        )

except ConfigError as e:
    logger.error("Konfigürasyon hatası: %s", e)
    send_telegram(f"❌ Bot başlatılamadı — konfigürasyon hatası:\n\n{e}")
    sys.exit(1)


DB_PATH = os.path.join(DATA_DIR, "hacim_radari.db")


# ==================== BORSA BAĞLANTISI + PAYLAŞIMLI RATE LIMITER ====================

exchange = ccxt.btcturk({"enableRateLimit": True})

_thread_local = threading.local()


def get_thread_exchange() -> ccxt.btcturk:
    """Her thread kendi ccxt nesnesini kullanır (ccxt'nin iç durumunu
    thread'ler arasında paylaşmamak için) — ama İSTEK ZAMANLAMASI aşağıdaki
    RateLimiter ile tüm thread'ler arasında ORTAK kontrol edilir."""
    exch = getattr(_thread_local, "exchange", None)
    if exch is None:
        exch = ccxt.btcturk({"enableRateLimit": True})
        _thread_local.exchange = exch
    return exch


class RateLimiter:
    """Tüm thread'lerin paylaştığı, borsanın izin verdiği hızı aşmayan basit
    bir istek aralayıcı. Önceki sürümde her thread kendi ccxt bağlantısında
    ayrı bir "rate limit" sayacı tuttuğu için thread'ler birbirinden habersiz
    kalıyor ve toplamda gerçek limit kat kat aşılabiliyordu. Bu sınıf tüm
    thread'lerin gördüğü TEK bir zamanlayıcıdır."""

    def __init__(self, min_interval_sec: float):
        self._min_interval = max(min_interval_sec, 0.0)
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            remaining = self._min_interval - (now - self._last_call)
            if remaining > 0:
                time.sleep(remaining)
            self._last_call = time.monotonic()


_default_rate_limit_sec = (exchange.rateLimit / 1000) if getattr(exchange, "rateLimit", 0) else 0.5
_rate_limit_sec = MIN_REQUEST_INTERVAL_SEC if MIN_REQUEST_INTERVAL_SEC >= 0 else _default_rate_limit_sec
rate_limiter = RateLimiter(_rate_limit_sec)

last_signal_time: Dict[str, float] = {}
last_signal_lock = threading.Lock()
last_anomaly_time: Dict[str, float] = {}
last_anomaly_lock = threading.Lock()


def safe_call(func, *args, retries: int = 3, delay: float = 3.0, **kwargs):
    """Sadece GERÇEK ağ hatalarında (ccxt.NetworkError: zaman aşımı, geçici
    kullanılamama, hız sınırı vb.) yeniden dener. "Geçersiz sembol" gibi
    tekrar denemekle asla düzelmeyecek hatalarda (ccxt.ExchangeError ve
    diğerleri) hemen bırakır — böylece hem zaman kaybetmeyiz hem de aynı
    kalıcı hatayı tur başına 3 kez tekrarlamayız."""
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except ccxt.NetworkError as e:
            last_err = e
            logger.warning(
                "Geçici ağ hatası (%s): %s. Tekrar deneniyor (%d/%d)...",
                getattr(func, "__name__", "call"), e, attempt + 1, retries,
            )
            time.sleep(delay)
        except Exception as e:
            logger.warning(
                "Kalıcı hata (%s), tekrar denenmiyor: %s", getattr(func, "__name__", "call"), e
            )
            raise
    raise last_err


def get_symbols(quote: str, exclude: set) -> List[str]:
    rate_limiter.wait()
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


def get_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    exch = get_thread_exchange()
    duration_sec = exch.parse_timeframe(timeframe)
    now_ms = exch.milliseconds()
    since = now_ms - (limit + 5) * duration_sec * 1000

    rate_limiter.wait()
    data = safe_call(exch.fetch_ohlcv, symbol, timeframe=timeframe, since=since, limit=limit)
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if len(df) > limit:
        df = df.iloc[-limit:].reset_index(drop=True)
    return df


# ==================== KALICI DEPOLAMA (SQLite) ====================
# CSV yerine SQLite: sorgulanabilir, atomik yazım. Ama DİKKAT: Railway'in
# dosya sistemi varsayılan olarak GEÇİCİDİR — DATA_DIR bir Volume'a
# yönlendirilmezse bu veritabanı da her deploy'da sıfırlanır. Gerçek kalıcılık
# için: Railway > servis > Settings > Volumes'tan bir volume ekle, mount
# path'ini (örn. /data) DATA_DIR ortam değişkenine yaz.

_db_lock = threading.Lock()


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                score REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                pct_change REAL NOT NULL,
                vol_ratio REAL NOT NULL
            )
        """)
        conn.commit()
    logger.info("Veritabanı hazır: %s", DB_PATH)


def log_signal(symbol: str, r: Dict[str, Any]) -> None:
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO signals (ts, symbol, price, score, stop_loss, take_profit) VALUES (?, ?, ?, ?, ?, ?)",
            (now_tr().strftime("%Y-%m-%d %H:%M:%S"), symbol, r["price"], r["score"], r["stop_loss"], r["take_profit"]),
        )
        conn.commit()


def log_anomaly(symbol: str, a: Dict[str, float]) -> None:
    with _db_lock, sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO anomalies (ts, symbol, price, pct_change, vol_ratio) VALUES (?, ?, ?, ?, ?)",
            (now_tr().strftime("%Y-%m-%d %H:%M:%S"), symbol, a["price"], a["pct_change"], a["vol_ratio"]),
        )
        conn.commit()


# ==================== GÖSTERGELER (saf fonksiyonlar, test edilebilir) ====================

def compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series]:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_adx(df: pd.DataFrame, period: int) -> pd.Series:
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


def find_recent_cross_up(a: pd.Series, b: pd.Series, lookback: int) -> Tuple[bool, Optional[int]]:
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


def is_breakout(df: pd.DataFrame, lookback: int) -> bool:
    if len(df) <= lookback + 1:
        return False
    window = df["close"].iloc[-(lookback + 1):-1]
    if window.isna().all():
        return False
    return df["close"].iloc[-1] > window.max()


def detect_anomaly(df: pd.DataFrame, threshold_pct: float, vol_mult: float, window: int) -> Optional[Dict[str, float]]:
    """Son `window` mumda ani fiyat sıçraması + hacim patlaması var mı?
    Trend onayı değildir, sadece olağandışı hareket tespitidir."""
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


def confidence_label(score: float) -> str:
    if score >= 80:
        return "🟢🟢🟢 Güçlü"
    elif score >= 65:
        return "🟢🟢 Orta-Güçlü"
    return "🟢 Standart"


# ==================== ANALİZ / STRATEJİ ====================

def evaluate(symbol: str) -> Optional[Dict[str, Any]]:
    fetch_limit = max(LONG_WINDOW, BREAKOUT_LOOKBACK, ADX_PERIOD * 2, 40) + CROSS_LOOKBACK + 5
    df = get_ohlcv(symbol, TIMEFRAME, limit=fetch_limit)

    if len(df) < LONG_WINDOW + 2:
        return None

    result: Dict[str, Any] = {"setup": None, "anomaly": None}
    price = df["close"].iloc[-1]
    score = 0.0
    details: List[str] = []

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

    anomaly = detect_anomaly(df, ANOMALY_PCT_THRESHOLD, ANOMALY_VOLUME_MULT, ANOMALY_WINDOW)
    if anomaly:
        result["anomaly"] = anomaly

    if result["setup"] is None and result["anomaly"] is None:
        return None
    return result


def get_market_trend() -> str:
    try:
        df = get_ohlcv(f"BTC/{QUOTE_CURRENCY}", TIMEFRAME, limit=LONG_WINDOW + 5)
        ma_short = df["close"].rolling(SHORT_WINDOW).mean().iloc[-1]
        ma_long = df["close"].rolling(LONG_WINDOW).mean().iloc[-1]
        return "yukarı" if ma_short > ma_long else "aşağı"
    except Exception:
        return "bilinmiyor"


def scan_once(symbols: List[str]) -> Tuple[List[Tuple[str, dict]], List[Tuple[str, dict]]]:
    now = time.time()
    setup_hits: List[Tuple[str, dict]] = []
    anomaly_hits: List[Tuple[str, dict]] = []

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        future_map = {executor.submit(evaluate, s): s for s in symbols}
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                result = future.result()
            except Exception as e:
                logger.warning("%s taranırken hata: %s", symbol, e)
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

def build_setup_card(symbol: str, r: Dict[str, Any]) -> str:
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


def build_anomaly_card(symbol: str, a: Dict[str, float]) -> str:
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


def send_setup_alerts(hits: List[Tuple[str, dict]], market_trend: str) -> None:
    if not hits:
        return
    market_note = ""
    if market_trend == "aşağı":
        market_note = "\n⚠️ <i>Genel piyasa (BTC) şu an düşüş eğiliminde — dikkatli olun</i>\n"
    cards = [build_setup_card(symbol, r) for symbol, r in hits]
    msg = f"🚀 <b>Binance TR — Yeni Setup'lar</b>{market_note}\n\n" + "\n\n".join(cards)
    logger.info("%d setup sinyali gönderiliyor", len(hits))
    send_telegram(msg)


def send_anomaly_alerts(hits: List[Tuple[str, dict]]) -> None:
    if not hits:
        return
    cards = [build_anomaly_card(symbol, a) for symbol, a in hits]
    msg = "⚡ <b>Anormal Hareket Tespiti</b>\n\n" + "\n\n".join(cards)
    logger.info("%d anomali uyarısı gönderiliyor", len(hits))
    send_telegram(msg)


# ==================== ZARİF KAPANMA ====================

_shutdown_requested = False


def _handle_shutdown_signal(signum, frame) -> None:
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Kapanma sinyali alındı (%s), güvenli şekilde durduruluyor...", signum)


def _sleep_with_shutdown_check(total_seconds: float, step: float = 1.0) -> None:
    remaining = total_seconds
    while remaining > 0 and not _shutdown_requested:
        time.sleep(min(step, remaining))
        remaining -= step


# ==================== ANA DÖNGÜ ====================

def run_bot() -> None:
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    init_db()

    try:
        symbols = get_symbols(QUOTE_CURRENCY, EXCLUDE_COINS)
    except Exception as e:
        error_detail = traceback.format_exc()
        logger.error(error_detail)
        send_telegram(f"❌ Bot başlarken hata aldı (coin listesi çekilemedi):\n\n{e}\n\n{error_detail}")
        raise

    persistence_note = (
        "✅ Kalıcı depolama aktif (DATA_DIR ayarlı)"
        if DATA_DIR != "."
        else "⚠️ DATA_DIR ayarlanmamış — sinyal geçmişi bir sonraki deploy'da sıfırlanacak"
    )

    start_msg = (
        f"🤖 <b>Profesyonel AL-sinyali botu başladı</b>\n"
        f"Kaynak: Binance TR (BtcTurk altyapısı)\n"
        f"Taranan: {len(symbols)} adet {QUOTE_CURRENCY} paritesi (paralel, {SCAN_WORKERS} işçi)\n"
        f"Zaman dilimi: {TIMEFRAME}\n"
        f"{persistence_note}\n\n"
        f"🎯 <b>Setup sinyali:</b> Trend + ADX + RSI + MACD + Hacim + Breakout\n"
        f"Min. skor: {MIN_SCORE}/100 | Güven: 🟢 Standart · 🟢🟢 Orta-Güçlü · 🟢🟢🟢 Güçlü\n\n"
        f"⚡ <b>Anomali uyarısı:</b> {ANOMALY_WINDOW} mumda %{ANOMALY_PCT_THRESHOLD:.0f}+ "
        f"sıçrama + {ANOMALY_VOLUME_MULT:.1f}x hacim (bilgi amaçlı, trend onayı değildir)\n\n"
        f"Tarama aralığı: {CHECK_INTERVAL_SEC} sn"
    )
    logger.info(start_msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    send_telegram(start_msg)

    last_symbol_refresh = time.time()
    SYMBOL_REFRESH_INTERVAL = 3600while not _shutdown_requested:
        try:
            now = time.time()
            if now - last_symbol_refresh > SYMBOL_REFRESH_INTERVAL:
                symbols = get_symbols(QUOTE_CURRENCY, EXCLUDE_COINS)
                last_symbol_refresh = now
                logger.info("Taranan coin listesi güncellendi (%d adet).", len(symbols))

            market_trend = get_market_trend()
            setup_hits, anomaly_hits = scan_once(symbols)

            send_setup_alerts(setup_hits, market_trend)
            send_anomaly_alerts(anomaly_hits)

            if not setup_hits and not anomaly_hits:
                logger.info("Kriterlere uyan coin yok. (Piyasa: %s)", market_trend)

            _sleep_with_shutdown_check(CHECK_INTERVAL_SEC)

        except Exception as e:
            error_detail = traceback.format_exc()
            logger.error(error_detail)
            try:
                send_telegram(f"⚠️ Bot hata aldı, 30 sn sonra tekrar denenecek:\n\n{e}\n\n{error_detail}")
            except Exception:
                pass
            _sleep_with_shutdown_check(30)

    logger.info("Bot durduruldu.")
    send_telegram("🛑 Bot durduruldu (yeniden başlatma/deploy nedeniyle olabilir).")


if __name__ == "__main__":
    run_bot()
