from __future__ import annotations

import logging
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

# --- kurulum -----------------------------------------------------------

SETTINGS.validate()

if not SETTINGS.admin_token:
    log.warning(
        "ADMIN_TOKEN ayarlanmamış -- /performance ve /admin/* endpoint'leri "
        "korumasız. Prod ortamda ADMIN_TOKEN ayarlamanız şiddetle önerilir."
    )

LIMITER = RateLimiter(SETTINGS.weight_budget_per_minute)
CLIENT = BinanceClient(SETTINGS, LIMITER)
DBS = DB(SETTINGS.db_path, retention_days=SETTINGS.signal_retention_days)
MARKET = MarketData(CLIENT, SETTINGS)


# --- yardımcılar ---------------------------------------------------------

def candidates(cfg: Settings, data: list) -> list[dict]:
    result = []
    for ticker in data:
        symbol = ticker.get("symbol", "")
        if not symbol.endswith("TRY") or symbol in cfg.excluded_symbols:
            continue
        try:
            volume = float(ticker.get("quoteVolume", 0))
            change = float(ticker.get("priceChangePercent", 0))
            price = float(ticker.get("lastPrice", 0))

            if volume < cfg.min_quote_volume or change > 25 or price <= 0:
                continue

            result.append({"symbol": symbol, "volume": volume, "chg": change, "price": price})
        except (TypeError, ValueError):
            continue
    return result


def shortlist(cfg: Settings, items: list[dict]) -> list[dict]:
    def rank(item):
        return item["volume"] * (1 + max(item["chg"], 0) / 100)

    return sorted(items, key=rank, reverse=True)[:cfg.shortlist_size]


def message(r: dict) -> str:
    title = "🔥 ÇOK GÜÇLÜ AL" if r["status"] == "VERY" else "🟢 AL"
    reasons = []

    if r["closed_breakout"]:
        reasons.append("Kapanış kırılımı")
    elif r["breakout"]:
        reasons.append("Direnç kırıldı")
    elif r["dist"] <= 0.35:
        reasons.append(f"Direnç %{r['dist']:.2f}")

    if r["vr"] >= 1.5:
        reasons.append(f"1m hacim {r['vr']:.1f}x")
    if r["vr5"] >= 1.5:
        reasons.append(f"5m hacim {r['vr5']:.1f}x")
    if r["impulse"] >= 2:
        reasons.append(f"İvme {r['impulse']:.1f}x")
    if r["bp"] >= 65:
        reasons.append(f"Alıcı %{r['bp']:.0f}")
    if r["ema"]:
        reasons.append("EMA trend")
    if r["macd"]:
        reasons.append("MACD güçleniyor")
    if r["hl"]:
        reasons.append("Higher-Low")
    if r["squeeze"]:
        reasons.append("BB sıkışma")
    if r["trades_1m"] >= SETTINGS.min_1m_trades:
        reasons.append("İşlem katılımı güçlü")

    trap = ""
    if r["trap"]:
        trap = "\n⚠️ TUZAK: " + ", ".join(r["trap_reasons"]) + "\n"

    if r["status"] == "VERY":
        result = "🚀 Güçlü teyit."
    elif r["closed_breakout"]:
        result = "🎯 Alım teyidi oluştu."
    else:
        result = "🟡 Kırılım teyidi bekleniyor."

    d30 = f"{r['d30']:+.1f}%" if r["d30"] is not None else "VERİ YOK"
    d90 = f"{r['d90']:+.1f}%" if r["d90"] is not None else "VERİ YOK"

    return (
        "🐋 BALİNA RADARI V23\n\n"
        f"{title}\n\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r['price']:.8g}\n"
        f"💪 Güç: {r['score']}/100\n"
        f"🏆 Öncelik: {r['priority']:.0f}/100\n"
        f"🎯 Giriş: {r['entry_quality']}/100\n"
        f"🔁 Teyit: {r['streak']}x\n\n"
        f"📊 1m Hacim: {r['vr']:.2f}x | 5m: {r['vr5']:.2f}x\n"
        f"🚀 İvme: {r['impulse']:.2f}x\n"
        f"🛒 Alıcı: %{r['bp']:.0f}\n"
        f"🔢 İşlem: {r['trades_1m']}\n"
        f"📈 RSI: {r['rv']:.0f} | ADX: {r['ad']:.0f}\n"
        f"🎯 Direnç: %{r['dist']:.2f}\n"
        f"🚀 Kırılım: {'✅' if r['breakout'] else '⏳'}\n"
        f"📅 30g: {d30} | 90g: {d90}\n"
        f"🌐 BTC/TRY: {r['market_momentum']:+.2f}%\n"
        f"{trap}\n"
        f"🔎 {' • '.join(reasons[:8])}\n\n"
        f"{result}"
    )


# --- tarama döngüsü --------------------------------------------------------

def scan() -> bool:
    start = time.time()
    data = CLIENT.tickers()

    if not data:
        return True

    price_map = {}
    for item in data:
        try:
            price_map[item.get("symbol")] = float(item.get("lastPrice", 0))
        except (TypeError, ValueError):
            continue

    DBS.update_outcomes(price_map, SETTINGS.outcome_window)

    all_candidates = candidates(SETTINGS, data)
    items = shortlist(SETTINGS, all_candidates)

    signals, stats = [], {}

    with ThreadPoolExecutor(max_workers=SETTINGS.workers) as executor:
        jobs = [executor.submit(analyze, SETTINGS, CLIENT, DBS, MARKET, item) for item in items]

        for job in as_completed(jobs):
            try:
                r = job.result()
            except Exception:
                r = {"status": "error"}

            status = r.get("status", "error")
            stats[status] = stats.get(status, 0) + 1

            if status in ("BUY", "VERY"):
                signals.append(r)

    signals = rank_signals(SETTINGS, signals)

    sent = 0
    for r in signals:
        if sent >= SETTINGS.max_signals:
            break
        if r["priority"] < SETTINGS.min_priority:
            continue
        if not DBS.can_send(r["symbol"], r["status"], SETTINGS.cooldown):
            continue

        if CLIENT.telegram(message(r)):
            DBS.put(
                r["symbol"], r["score"], r["status"], r["status"],
                sent=time.time(), streak=r["streak"], trap=r["trap"], priority=r["priority"],
            )
            DBS.create_signal(r)
            sent += 1

        time.sleep(0.3)

    elapsed = time.time() - start
    errors = stats.get("error", 0)

    log.info(
        "V23 | TRY:%d/%d | AL:%d | VERY:%d | Hata:%d | Gönder:%d | %.1fs | budget:%s",
        len(items), len(all_candidates), stats.get("BUY", 0), stats.get("VERY", 0),
        errors, sent, elapsed, LIMITER.snapshot(),
    )

    return errors / max(1, len(items)) > 0.30 or elapsed > SETTINGS.scan_interval * 1.25


def performance() -> dict:
    rows = DBS.performance_summary()
    if not rows:
        return {"samples": 0, "note": "Henüz tamamlanmış sinyal yok."}

    completed = [r for r in rows if r[6] is not None]

    def stats(data):
        done = [r for r in data if r[6] is not None]
        if not done:
            return {"samples": len(data), "completed": 0}
        return {
            "samples": len(data),
            "completed": len(done),
            "avg_15m_pct": round(avg([r[6] for r in done]), 2),
            "positive_15m_pct": round(sum(r[6] > 0 for r in done) / len(done) * 100, 1),
        }

    result = {
        "samples": len(rows),
        "completed_15m": len(completed),
        "avg_max_pct": round(avg([r[3] for r in rows]), 2),
        "avg_min_pct": round(avg([r[4] for r in rows]), 2),
        "avg_15m_pct": round(avg([r[6] for r in completed]), 2) if completed else 0,
    }

    result["score"] = {
        "68_75": stats([r for r in rows if 68 <= r[0] < 76]),
        "76_83": stats([r for r in rows if 76 <= r[0] < 84]),
        "84_90": stats([r for r in rows if 84 <= r[0] < 91]),
        "91_100": stats([r for r in rows if r[0] >= 91]),
    }
    result["level"] = {
        "BUY": stats([r for r in rows if r[7] == "BUY"]),
        "VERY": stats([r for r in rows if r[7] == "VERY"]),
    }
    result["entry_quality"] = {
        "0_49": stats([r for r in rows if r[8] < 50]),
        "50_69": stats([r for r in rows if 50 <= r[8] < 70]),
        "70_84": stats([r for r in rows if 70 <= r[8] < 85]),
        "85_100": stats([r for r in rows if r[8] >= 85]),
    }
    return result


def validate_market() -> None:
    info = CLIENT.exchange_info()
    symbols = {x.get("symbol") for x in info.get("symbols", [])}
    try_count = sum(s.endswith("TRY") for s in symbols if s)

    if try_count <= 0:
        raise RuntimeError(f"BASE {SETTINGS.base_url} üzerinde TRY marketi bulunamadı.")

    if SETTINGS.market_symbol not in symbols:
        log.warning("%s bulunamadı; BTC filtresi devre dışı.", SETTINGS.market_symbol)

    log.info("V23 | Binance TR doğrulandı | TRY:%d", try_count)


def loop() -> None:
    log.info("🐋 BALİNA RADARI V23 başlatılıyor...")

    try:
        validate_market()
    except Exception as e:
        log.exception("MARKET DOĞRULAMA HATASI: %s", e)
        return

    if SETTINGS.telegram_token and SETTINGS.telegram_chat:
        CLIENT.telegram(
            "🐋 BALİNA RADARI V23 AKTİF\n"
            "🏆 Öncelik sistemi aktif\n"
            "⚠️ TRAP filtresi aktif\n"
            "🛡️ Rate-limit koruması aktif"
        )

    last_cleanup = 0.0

    while True:
        started = time.time()

        try:
            backoff = scan()
        except Exception:
            log.exception("Tarama döngüsü hatası")
            backoff = True

        # Eski kodda hiç yoktu: signals tablosu günde bir kere temizlenir.
        if started - last_cleanup > 86400:
            try:
                removed = DBS.cleanup_old_signals()
                if removed:
                    log.info("Retention: %d eski sinyal silindi.", removed)
            except Exception:
                log.exception("Retention temizliği başarısız")
            last_cleanup = started

        elapsed = time.time() - started

        if backoff:
            time.sleep(max(180, SETTINGS.scan_interval * 3))
        else:
            time.sleep(max(1, SETTINGS.scan_interval - elapsed))


# --- Flask ---------------------------------------------------------------

app = Flask(__name__)


def require_admin(fn):
    """Eski kodda /performance tamamen açıktı. ADMIN_TOKEN set edilmişse
    bu decorator `X-Admin-Token` header'ını zorunlu kılar."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if SETTINGS.admin_token:
            if request.headers.get("X-Admin-Token") != SETTINGS.admin_token:
                abort(401)
        return fn(*args, **kwargs)
    return wrapper


@app.route("/")
def home():
    return "🐋 Balina Radarı V23 Aktif"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V23",
        "base": SETTINGS.base_url,
        "scan_interval": SETTINGS.scan_interval,
        "workers": SETTINGS.workers,
        "rate_limit": LIMITER.snapshot(),
    }


@app.route("/performance")
@require_admin
def performance_route():
    return performance()


Thread(target=loop, daemon=True, name="balina-v23").start()


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), use_reloader=False)

"""
Merkezi konfigürasyon.

Eski versiyonda 40+ env var modül seviyesinde, gevşek tiplerle okunuyordu.
Yanlış/eksik bir değer (örn. MAX_WORKERS="abc") import anında anlaşılmaz bir
ValueError ile patlıyordu ve hangi ayarın bozuk olduğunu anlamak zordu.

Burada tek bir dataclass'ta topluyoruz, hepsini tip dönüşümüyle okuyoruz ve
mantıksal tutarlılığı (örn. TOP_PRIORITY <= SHORTLIST) doğruluyoruz.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name}='{raw}' geçerli bir tam sayı değil") from e


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name}='{raw}' geçerli bir sayı değil") from e


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    base_url: str = field(default_factory=lambda: _env_str(
        "BINANCE_TR_BASE", "https://api.binance.me"))
    scan_interval: int = field(default_factory=lambda: _env_int("SCAN_INTERVAL", 30))
    workers: int = field(default_factory=lambda: _env_int("MAX_WORKERS", 10))
    max_signals: int = field(default_factory=lambda: _env_int("MAX_SIGNALS_PER_SCAN", 3))
    cooldown: int = field(default_factory=lambda: _env_int("SIGNAL_COOLDOWN", 1200))
    min_quote_volume: float = field(default_factory=lambda: _env_float("MIN_QUOTE_VOLUME_TRY", 1_000_000))
    shortlist_size: int = field(default_factory=lambda: _env_int("SHORTLIST_SIZE", 80))
    request_timeout: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT", 8))
    db_path: str = field(default_factory=lambda: _env_str("STATE_DB_PATH", "balina_v23.db"))
    outcome_window: int = field(default_factory=lambda: _env_int("OUTCOME_WINDOW", 900))
    signal_retention_days: int = field(default_factory=lambda: _env_int("SIGNAL_RETENTION_DAYS", 30))

    telegram_token: str = field(default_factory=lambda: _env_str("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat: str = field(default_factory=lambda: _env_str("TELEGRAM_CHAT_ID", ""))

    lt30_mild: float = field(default_factory=lambda: _env_float("LT30_MILD", -20))
    lt30_strong: float = field(default_factory=lambda: _env_float("LT30_STRONG", -35))
    lt90_mild: float = field(default_factory=lambda: _env_float("LT90_MILD", -30))
    lt90_strong: float = field(default_factory=lambda: _env_float("LT90_STRONG", -50))
    lt90_extreme: float = field(default_factory=lambda: _env_float("LT90_EXTREME", -65))

    daily_cache_ttl: int = field(default_factory=lambda: _env_int("DAILY_CACHE_TTL", 900))

    min_1m_trades: int = field(default_factory=lambda: _env_int("MIN_1M_TRADES", 20))
    min_5m_trades: int = field(default_factory=lambda: _env_int("MIN_5M_TRADES", 50))
    trade_reference: int = field(default_factory=lambda: _env_int("TRADE_REFERENCE", 100))

    streak_window: int = field(default_factory=lambda: _env_int("STREAK_WINDOW", 180))
    buy_streak: int = field(default_factory=lambda: _env_int("BUY_STREAK", 2))
    very_streak: int = field(default_factory=lambda: _env_int("VERY_STREAK", 2))

    market_symbol: str = field(default_factory=lambda: _env_str("MARKET_SYMBOL", "BTCTRY"))
    market_move: float = field(default_factory=lambda: _env_float("MARKET_MOVE", 2))

    top_priority: int = field(default_factory=lambda: _env_int("TOP_PRIORITY", 5))
    min_priority: int = field(default_factory=lambda: _env_int("MIN_PRIORITY", 60))

    trap_buyer: float = field(default_factory=lambda: _env_float("TRAP_BUYER", 50))
    trap_volume: float = field(default_factory=lambda: _env_float("TRAP_VOLUME", 1.8))
    trap_momentum: float = field(default_factory=lambda: _env_float("TRAP_MOMENTUM", -1.2))

    # Binance ağırlık bütçesi: dakikalık istek ağırlığı limiti (bkz. rate_limiter.py)
    weight_budget_per_minute: int = field(default_factory=lambda: _env_int("WEIGHT_BUDGET_PER_MINUTE", 1000))

    # /performance gibi iç endpoint'leri korumak için basit paylaşılan anahtar.
    # Boş bırakılırsa (varsayılan DEĞİL, bilinçli olarak) endpoint korumasız kalır
    # ve bunu loop() başlangıcında uyarı olarak loglarız.
    admin_token: str = field(default_factory=lambda: _env_str("ADMIN_TOKEN", ""))

    excluded_symbols: frozenset = field(default_factory=lambda: frozenset({
        "USDTTRY", "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
    }))

    def validate(self) -> None:
        problems = []

        if self.workers < 1:
            problems.append("MAX_WORKERS >= 1 olmalı")
        if self.shortlist_size < 1:
            problems.append("SHORTLIST_SIZE >= 1 olmalı")
        if self.scan_interval < 5:
            problems.append("SCAN_INTERVAL çok düşük (>=5 önerilir)")
        if self.max_signals < 1:
            problems.append("MAX_SIGNALS_PER_SCAN >= 1 olmalı")
        if self.request_timeout < 1:
            problems.append("REQUEST_TIMEOUT >= 1 olmalı")
        if not (0 < self.min_priority <= 100):
            problems.append("MIN_PRIORITY 0-100 arasında olmalı")
        if self.weight_budget_per_minute < 100:
            problems.append("WEIGHT_BUDGET_PER_MINUTE çok düşük, tarama hiç ilerlemez")

        if problems:
            raise ValueError(
                "Konfigürasyon hatası:\n- " + "\n- ".join(problems)
            )


SETTINGS = Settings()
  """
Eski koddaki `analyze()` tek fonksiyonda ~250 satırdı: 5m/1m veri çekme,
indikatör hesaplama, trap tespiti, setup/confirmation/penalty/entry
puanlama ve stage kararı hepsi iç içeydi. Sonuç: hiçbir alt parça tek
başına test edilemiyordu.

Burada aynı MANTIK korunarak (davranış değiştirilmedi) adımlar ayrı, saf
fonksiyonlara bölündü. `analyze()` artık sadece bu adımları sırayla
çağıran bir orkestratör -- indikatör hesaplama, puanlama ve stage kararı
birbirinden bağımsız test edilebilir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from binance_client import BinanceClient
from config import Settings
from db import DB
from indicators import adx, avg, bb, clamp, ema, macd, pct, rsi
from market import MarketData

log = logging.getLogger("balina.scoring")


@dataclass
class Features:
    price: float
    momentum1: float
    momentum5: float
    location: float
    close_position: float
    vr: float
    vr5: float
    impulse: float
    bp: float
    trades1: int
    trades5: int
    ema_up: bool
    ema_cross: bool
    price_above_ema50: bool
    rv: float
    old_rsi: float
    macd_up: bool
    ad: float
    plus_di: float
    minus_di: float
    squeeze: bool
    expanding: bool
    dist: float
    breakout: bool
    closed_breakout: bool
    higher_low: bool
    low_activity: bool
    weak_volume: bool
    trap: bool
    trap_reasons: list[str] = field(default_factory=list)


def long_term_penalty(cfg: Settings, d30: float, d90: float) -> int:
    penalty = 0
    if d30 <= cfg.lt30_strong:
        penalty -= 8
    elif d30 <= cfg.lt30_mild:
        penalty -= 4

    if d90 <= cfg.lt90_extreme:
        penalty -= 15
    elif d90 <= cfg.lt90_strong:
        penalty -= 10
    elif d90 <= cfg.lt90_mild:
        penalty -= 5

    return penalty


def trade_confidence(cfg: Settings, trades: int, volume_ratio: float) -> float:
    if trades <= 0:
        return 0
    if volume_ratio >= 2 and trades < cfg.min_1m_trades:
        return 0.25
    if trades < cfg.min_1m_trades:
        return 0.40
    return min(1.0, max(0.40, trades / cfg.trade_reference))


def extract_features(cfg: Settings, c1: list, close5: list[float], volume5: list[float],
                      price_5m_close: float, trades5_sum: int) -> Features:
    """1m kline'lardan tüm türetilmiş metrikleri ve indikatörleri hesaplar."""
    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]
    trades = [int(x[8]) for x in c1]

    price = close[-1]

    avg5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])
    vr5 = recent5 / avg5 if avg5 else 0
    momentum5 = pct(close5[-4], price)

    momentum1 = pct(close[-2], price)

    low90 = min(low[-90:])
    high90 = max(high[-90:])
    location = (price - low90) / (high90 - low90) * 100 if high90 > low90 else 50

    avg_volume = avg(volume[-30:])
    last3 = avg(volume[-3:])
    previous = avg(volume[-10:-3])
    vr = last3 / avg_volume if avg_volume else 0
    impulse = min(last3 / previous if previous else 1, 10)

    buy_volume = sum(float(x[10]) for x in c1[-5:])
    total_volume = sum(float(x[7]) for x in c1[-5:])
    bp = buy_volume / total_volume * 100 if total_volume else 50

    trades1 = sum(trades[-5:])

    ema9, ema21, ema50 = ema(close, 9), ema(close, 21), ema(close, 50)
    ema9_old, ema21_old = ema(close[:-3], 9), ema(close[:-3], 21)
    ema_up = ema9 > ema21 and ema9 > ema9_old
    ema_cross = ema9 > ema21 and ema9_old <= ema21_old

    rv = rsi(close)
    old_rsi = rsi(close[:-3])

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])
    macd_up = macd_now > macd_old

    ad, plus_di, minus_di = adx(high, low, close)

    lower, middle, upper = bb(close)
    width = (upper - lower) / middle * 100 if middle else 0
    old_lower, old_middle, old_upper = bb(close[:-5])
    old_width = (old_upper - old_lower) / old_middle * 100 if old_middle else width

    squeeze = width <= 2.2 or (old_width > 0 and width < old_width * 0.80)
    expanding = old_width > 0 and width > old_width * 1.08

    resistance = max(high[-30:-2])
    dist = max(0, (resistance - price) / price * 100)
    breakout = price > resistance
    closed_breakout = close[-1] > resistance

    higher_low = low[-1] > low[-3] and low[-3] >= low[-6]

    candle_range = high[-1] - low[-1]
    close_position = (close[-1] - low[-1]) / candle_range * 100 if candle_range > 0 else 50

    low_activity = trades1 < cfg.min_1m_trades or trades5_sum < cfg.min_5m_trades
    weak_volume = vr < 1.0 or vr5 < 1.0

    trap_reasons = []
    if bp < cfg.trap_buyer and vr >= cfg.trap_volume:
        trap_reasons.append("zayıf alıcı")
    if momentum5 < cfg.trap_momentum and not higher_low:
        trap_reasons.append("negatif momentum")
    if low_activity and vr >= 2:
        trap_reasons.append("düşük işlem")
    if low_activity and weak_volume and bp >= 90:
        trap_reasons.append("güvenilmez baskı")

    return Features(
        price=price, momentum1=momentum1, momentum5=momentum5, location=location,
        close_position=close_position, vr=vr, vr5=vr5, impulse=impulse, bp=bp, trades1=trades1, trades5=trades5_sum,
        ema_up=ema_up, ema_cross=ema_cross, price_above_ema50=price >= ema50,
        rv=rv, old_rsi=old_rsi, macd_up=macd_up, ad=ad, plus_di=plus_di, minus_di=minus_di,
        squeeze=squeeze, expanding=expanding, dist=dist, breakout=breakout,
        closed_breakout=closed_breakout, higher_low=higher_low,
        low_activity=low_activity, weak_volume=weak_volume,
        trap=bool(trap_reasons), trap_reasons=trap_reasons,
    )


def score_setup(cfg: Settings, f: Features) -> int:
    setup = 0
    if f.ema_up:
        setup += 12
    if f.ema_cross:
        setup += 6
    if f.squeeze:
        setup += 8
    if f.higher_low:
        setup += 6
    if 35 <= f.rv <= 65 and f.rv > f.old_rsi:
        setup += 8
    if f.price_above_ema50:
        setup += 5
    if f.dist <= 0.70:
        setup += 8
    if f.vr >= 1.5 and f.trades1 >= cfg.min_1m_trades:
        setup += 8
    if f.bp >= 58 and f.trades1 >= cfg.min_1m_trades:
        setup += 5
    return setup


def score_confirmation(cfg: Settings, f: Features) -> int:
    confirmation = 0
    if f.closed_breakout:
        confirmation += 18
    elif f.breakout:
        confirmation += 10

    if f.vr >= 2:
        confirmation += 12
    elif f.vr >= 1.5:
        confirmation += 7

    if f.vr5 >= 1.5:
        confirmation += 8

    if f.bp >= 65 and f.trades1 >= cfg.min_1m_trades:
        confirmation += 7

    if f.macd_up:
        confirmation += 6

    if f.plus_di > f.minus_di and f.ad >= 18:
        confirmation += 7

    if f.close_position >= 65:
        confirmation += 4

    if f.expanding:
        confirmation += 4

    if f.trades1 >= cfg.min_1m_trades and f.trades5 >= cfg.min_5m_trades:
        confirmation += 3

    if f.weak_volume:
        confirmation -= 8
    if f.ad < 10:
        confirmation -= 10
    if f.low_activity:
        confirmation -= 8

    return confirmation


def score_penalty(cfg: Settings, f: Features, d30: float | None, d90: float | None,
                   market_momentum: float) -> int:
    penalty = long_term_penalty(cfg, d30, d90) if d30 is not None and d90 is not None else -5

    if f.momentum1 > 2.5:
        penalty -= 10
    if f.momentum5 > 5:
        penalty -= 12
    if f.rv > 78:
        penalty -= 10
    if f.rv >= 85:
        penalty -= 8
    if f.bp < 50 and f.vr >= 1.8:
        penalty -= 8
    if f.momentum5 < -1.2 and not f.higher_low:
        penalty -= 12
    if f.vr >= 2 and f.trades1 < cfg.min_1m_trades:
        penalty -= 8
    if f.trap:
        penalty -= 12

    if d90 is not None:
        if d90 <= cfg.lt90_extreme:
            penalty -= 8
        elif d90 <= cfg.lt90_strong:
            penalty -= 5

    if abs(market_momentum) >= cfg.market_move * 2:
        penalty -= 8
    elif abs(market_momentum) >= cfg.market_move:
        penalty -= 4

    return penalty


def score_entry_quality(cfg: Settings, f: Features, d30: float | None, d90: float | None) -> int:
    entry = 100

    if f.rv >= 85:
        entry -= 30
    elif f.rv >= 78:
        entry -= 15

    if f.momentum1 >= 5:
        entry -= 25
    elif f.momentum1 >= 2.5:
        entry -= 12

    if f.momentum5 >= 5:
        entry -= 20
    elif f.momentum5 >= 3:
        entry -= 10

    if f.dist <= 0.15:
        entry -= 8
    elif f.dist <= 0.35:
        entry -= 4

    if f.closed_breakout:
        entry += 5
    if f.higher_low:
        entry += 5

    if f.trades1 < cfg.min_1m_trades:
        entry -= 18
    if f.trades1 < 5:
        entry -= 15

    if f.vr < 1.0:
        entry -= 12
    if f.vr5 < 1.0:
        entry -= 10
    if f.ad < 10:
        entry -= 12

    if d30 is not None and d30 >= 20:
        entry -= 5
    if d90 is not None and d90 <= cfg.lt90_strong:
        entry -= 10

    if f.trap:
        entry -= 20

    return max(0, min(100, int(round(entry))))


def decide_stage(cfg: Settings, f: Features, score: int, setup: int, confirmation: int,
                  d30: float | None, d90: float | None) -> str:
    stage = "NONE"

    if setup >= 25:
        stage = "SETUP"

    if score >= 68 and confirmation >= 18 and not f.weak_volume:
        stage = "CONFIRMED"

    very_ok = (
        d30 is not None and d90 is not None
        and d30 > cfg.lt30_strong and d90 > cfg.lt90_strong
        and not f.low_activity and not f.weak_volume
        and f.ad >= 18 and f.rv < 85 and not f.trap
    )

    if (score >= 84 and confirmation >= 28 and f.vr >= 1.5 and f.vr5 >= 1.0
            and very_ok and f.closed_breakout):
        stage = "VERY"

    return stage


def trend_state_label(trend_ok: bool, d30: float | None, d90: float | None, cfg: Settings) -> str:
    if not trend_ok:
        return "VERİ YOK"
    if d30 > 10 and d90 > 0:
        return "POZİTİF TREND"
    if d90 <= cfg.lt90_extreme or d30 <= cfg.lt30_strong:
        return "YÜKSEK DÜŞÜŞ RİSKİ"
    if d90 <= cfg.lt90_strong or d30 <= cfg.lt30_mild:
        return "DÜŞÜŞ RİSKİ"
    return "NÖTR"


def analyze(cfg: Settings, client: BinanceClient, db: DB, market: MarketData, item: dict) -> dict:
    """Orkestratör: veri çeker, özellik çıkarır, puanlar, stage'e karar verir."""
    symbol = item["symbol"]

    try:
        k5 = client.klines(symbol, "5m", 80)
        if len(k5) < 40:
            return {"status": "PASS"}

        c5 = k5[:-1]
        close5 = [float(x[4]) for x in c5]
        volume5 = [float(x[7]) for x in c5]
        trades5_list = [int(x[8]) for x in c5]

        price_estimate = close5[-1]
        avg5 = avg(volume5[-12:])
        recent5 = avg(volume5[-3:])
        vr5_early = recent5 / avg5 if avg5 else 0
        momentum5_early = pct(close5[-4], price_estimate)

        if momentum5_early < -3 and vr5_early < 1.3:
            return {"status": "PASS"}

        trend = market.daily_trend(symbol)
        d30 = trend["d30"] if trend["ok"] else None
        d90 = trend["d90"] if trend["ok"] else None

        k1 = client.klines(symbol, "1m", 180)
        if len(k1) < 100:
            return {"status": "PASS"}

        c1 = k1[:-1]
        trades5_sum = sum(trades5_list[-1:])

        f = extract_features(cfg, c1, close5, volume5, price_estimate, trades5_sum)

        setup = score_setup(cfg, f)
        confirmation = score_confirmation(cfg, f)
        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0)
        penalty = score_penalty(cfg, f, d30, d90, market_momentum)

        score = clamp(setup + confirmation + penalty)

        if f.low_activity:
            score = min(score, 78)
        if f.weak_volume:
            score = min(score, 82)
        if f.ad < 10:
            score = min(score, 72)
        if f.rv >= 90 and f.trades1 < cfg.min_1m_trades:
            score = min(score, 65)
        if d90 is not None and d90 <= cfg.lt90_extreme:
            score = min(score, 82)

        entry = score_entry_quality(cfg, f, d30, d90)
        stage = decide_stage(cfg, f, score, setup, confirmation, d30, d90)

        level = {"VERY": "VERY", "CONFIRMED": "BUY", "SETUP": "INTERNAL"}.get(stage, "PASS")
        qualified = stage in ("SETUP", "CONFIRMED", "VERY") and not f.trap

        streak = db.update_streak(symbol, qualified, f.trap)

        if level == "BUY" and streak < cfg.buy_streak:
            level = "INTERNAL"
        if level == "VERY" and streak < cfg.very_streak:
            level = "INTERNAL"

        return {
            "status": level,
            "symbol": symbol,
            "score": score,
            "setup": setup,
            "confirmation": confirmation,
            "penalty": penalty,
            "price": f.price,
            "chg": item["chg"],
            "loc": f.location,
            "bp": f.bp,
            "vr": f.vr,
            "vr5": f.vr5,
            "impulse": f.impulse,
            "rv": f.rv,
            "ad": f.ad,
            "dist": f.dist,
            "ema": f.ema_up,
            "macd": f.macd_up,
            "squeeze": f.squeeze,
            "hl": f.higher_low,
            "breakout": f.breakout,
            "closed_breakout": f.closed_breakout,
            "trades_1m": f.trades1,
            "trades_5m": f.trades5,
            "trade_conf": trade_confidence(cfg, f.trades1, f.vr),
            "d30": d30,
            "d90": d90,
            "trend_state": trend_state_label(trend["ok"], d30, d90, cfg),
            "trap": f.trap,
            "trap_reasons": f.trap_reasons,
            "entry_quality": entry,
            "streak": streak,
            "market_momentum": market_momentum,
            "market_state": market_ctx.get("state", "VERİ YOK"),
        }

    except Exception as e:
        log.debug("%s: %s", symbol, e)
        return {"status": "error", "symbol": symbol}


def priority_score(cfg: Settings, r: dict) -> float:
    value = (
        r["score"] * 0.50
        + r["entry_quality"] * 0.25
        + r["trade_conf"] * 100 * 0.10
    )

    if r["streak"] >= 3:
        value += 8
    elif r["streak"] >= 2:
        value += 4

    if r["closed_breakout"]:
        value += 8
    elif r["breakout"]:
        value += 2

    if r["bp"] >= 75:
        value += 5
    elif r["bp"] >= 65:
        value += 3

    if r["vr"] >= 3:
        value += 5
    elif r["vr"] >= 2:
        value += 3
    elif r["vr"] >= 1.5:
        value += 1

    if r["vr5"] >= 2:
        value += 4
    elif r["vr5"] >= 1.5:
        value += 2

    if r["trades_1m"] < 5:
        value -= 15
    elif r["trades_1m"] < cfg.min_1m_trades:
        value -= 8

    if r["vr5"] < 0.75:
        value -= 8
    if r["ad"] < 10:
        value -= 8
    if r["rv"] >= 85:
        value -= 8

    d90 = r["d90"] if r["d90"] is not None else 0
    d30 = r["d30"] if r["d30"] is not None else 0

    if d90 <= cfg.lt90_extreme:
        value -= 12
    elif d90 <= cfg.lt90_strong:
        value -= 8
    elif d90 <= cfg.lt90_mild:
        value -= 4

    if d30 <= cfg.lt30_strong:
        value -= 6
    elif d30 <= cfg.lt30_mild:
        value -= 3

    if r["trap"]:
        value -= 25

    return max(0, min(100, round(value, 1)))


def rank_signals(cfg: Settings, signals: list[dict]) -> list[dict]:
    for r in signals:
        r["priority"] = priority_score(cfg, r)

    signals.sort(key=lambda x: (x["priority"], x["entry_quality"], x["score"]), reverse=True)

    for i, r in enumerate(signals, 1):
        r["rank"] = i

    return signals
  """
Eski koddaki DB sınıfının 3 temel sorunu:

1. Her çağrıda yeni `sqlite3.connect()` açılıp kapanıyordu -- connection
   kurulumu ucuz değil, ve WAL modu her seferinde yeniden PRAGMA ile
   ayarlanıyordu (aslında ayarlanmıyordu bile, sadece ilk __init__'te).
2. Tek global `Lock()` TÜM okuma/yazma işlemlerini serileştiriyordu.
   WAL modu concurrent read + single writer'a izin verir, ama global lock
   bunu zaten iptal ediyordu.
3. `signals` tablosunda index yoktu ve hiç temizlenmiyordu -- zamanla
   `update_outcomes` içindeki `WHERE ts > ?` sorgusu tam tablo taraması
   yapmaya başlar.

Çözüm: thread-local connection (her worker thread kendi bağlantısını
tutar, sqlite3 nesneleri thread-safe değildir bu yüzden paylaşılamaz),
yazma işlemleri için ayrı ve dar kapsamlı bir lock (yalnızca INSERT/UPDATE
sırasında), `ts` ve `symbol` üzerinde index, ve periyodik retention.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager


class DB:
    def __init__(self, path: str, retention_days: int = 30):
        self.path = path
        self.retention_days = retention_days
        self._write_lock = threading.Lock()
        self._local = threading.local()

        # Şema kurulumu bir kere, ana thread'den.
        with self._connect() as db:
            self._init_schema(db)

    # -- connection management -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _conn(self) -> sqlite3.Connection:
        """Bu thread için tek, tekrar kullanılan bağlantı."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    @contextmanager
    def _write(self):
        """Yazma işlemleri için: dar kapsamlı lock + otomatik commit/rollback."""
        with self._write_lock:
            conn = self._conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # -- schema -------------------------------------------------------------

    def _init_schema(self, db: sqlite3.Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS state(
                symbol TEXT PRIMARY KEY,
                sent REAL DEFAULT 0,
                score REAL DEFAULT 0,
                level TEXT DEFAULT 'NONE',
                stage TEXT DEFAULT 'NONE',
                updated REAL DEFAULT 0,
                streak INTEGER DEFAULT 0,
                streak_at REAL DEFAULT 0,
                trap INTEGER DEFAULT 0,
                priority REAL DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS signals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                ts REAL,
                price REAL,
                score REAL,
                setup REAL,
                confirmation REAL,
                penalty REAL,
                status TEXT,
                max_pct REAL DEFAULT 0,
                min_pct REAL DEFAULT 0,
                c1 REAL, c3 REAL, c5 REAL, c15 REAL,
                entry_quality REAL DEFAULT 0,
                priority REAL DEFAULT 0,
                d30 REAL DEFAULT 0,
                d90 REAL DEFAULT 0,
                trade_1m REAL DEFAULT 0,
                trade_5m REAL DEFAULT 0,
                market_momentum REAL DEFAULT 0,
                trap INTEGER DEFAULT 0
            )
        """)

        # Eski kodda yoktu -- update_outcomes ve performance_summary'nin
        # asıl darboğazı bu ikisi.
        db.execute("CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_signals_c15 ON signals(c15)")

        db.commit()

    # -- state table ----------------------------------------------------------

    def get(self, symbol: str):
        conn = self._conn()
        return conn.execute("""
            SELECT sent, score, level, stage, updated, streak, streak_at, trap, priority
            FROM state WHERE symbol=?
        """, (symbol,)).fetchone()

    def put(self, symbol, score, level, stage, sent=None,
            streak=None, trap=None, priority=None) -> None:
        with self._write() as db:
            old = db.execute(
                "SELECT sent, streak, trap, priority FROM state WHERE symbol=?",
                (symbol,)
            ).fetchone()

            now = time.time()
            sent_time = now if sent is not None else (old[0] if old else 0)
            old_streak = old[1] if old else 0
            old_trap = old[2] if old else 0
            old_priority = old[3] if old else 0

            db.execute("""
                INSERT INTO state(symbol, sent, score, level, stage, updated,
                                   streak, streak_at, trap, priority)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    sent=excluded.sent, score=excluded.score, level=excluded.level,
                    stage=excluded.stage, updated=excluded.updated,
                    streak=excluded.streak, streak_at=excluded.streak_at,
                    trap=excluded.trap, priority=excluded.priority
            """, (
                symbol, sent_time, score, level, stage, now,
                old_streak if streak is None else streak,
                now,
                old_trap if trap is None else int(trap),
                old_priority if priority is None else priority,
            ))

    def update_streak(self, symbol: str, qualified: bool, trap: bool = False) -> int:
        now = time.time()
        with self._write() as db:
            row = db.execute(
                "SELECT streak, streak_at FROM state WHERE symbol=?", (symbol,)
            ).fetchone()

            old_streak = int(row[0] or 0) if row else 0
            old_time = float(row[1] or 0) if row else 0

            if not qualified:
                streak = 0
            elif old_time and now - old_time <= 180:
                streak = old_streak + 1
            else:
                streak = 1

            db.execute("""
                INSERT INTO state(symbol, streak, streak_at, trap, updated)
                VALUES(?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    streak=excluded.streak, streak_at=excluded.streak_at,
                    trap=excluded.trap, updated=excluded.updated
            """, (symbol, streak, now, int(trap), now))

            return streak

    def can_send(self, symbol: str, level: str, cooldown: int) -> bool:
        row = self.get(symbol)
        if not row:
            return True

        sent = float(row[0] or 0)
        old_level = row[2]
        rank = {"BUY": 1, "VERY": 2}

        return (
            time.time() - sent >= cooldown
            or rank.get(level, 0) > rank.get(old_level, 0)
        )

    # -- signals table --------------------------------------------------------

    def create_signal(self, r: dict) -> int:
        with self._write() as db:
            cur = db.execute("""
                INSERT INTO signals(
                    symbol, ts, price, score, setup, confirmation, penalty,
                    status, entry_quality, priority, d30, d90,
                    trade_1m, trade_5m, market_momentum, trap
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["symbol"], time.time(), r["price"], r["score"],
                r["setup"], r["confirmation"], r["penalty"], r["status"],
                r.get("entry_quality", 0), r.get("priority", 0),
                r.get("d30", 0), r.get("d90", 0),
                r.get("trades_1m", 0), r.get("trades_5m", 0),
                r.get("market_momentum", 0), int(r.get("trap", False)),
            ))
            return cur.lastrowid

    def update_outcomes(self, price_map: dict, outcome_window: int) -> None:
        now = time.time()
        with self._write() as db:
            rows = db.execute("""
                SELECT id, symbol, ts, price, max_pct, min_pct, c1, c3, c5, c15
                FROM signals WHERE ts > ?
            """, (now - outcome_window,)).fetchall()

            for (sid, symbol, ts, price, max_pct, min_pct, c1, c3, c5, c15) in rows:
                current = price_map.get(symbol)
                if not current or not price or price <= 0:
                    continue

                change = (current - price) / price * 100
                updates = {"max_pct": max(max_pct, change), "min_pct": min(min_pct, change)}
                elapsed = now - ts

                if elapsed >= 60 and c1 is None:
                    updates["c1"] = change
                if elapsed >= 180 and c3 is None:
                    updates["c3"] = change
                if elapsed >= 300 and c5 is None:
                    updates["c5"] = change
                if elapsed >= 900 and c15 is None:
                    updates["c15"] = change

                clause = ", ".join(f"{k}=?" for k in updates)
                db.execute(f"UPDATE signals SET {clause} WHERE id=?",
                           (*updates.values(), sid))

    def cleanup_old_signals(self) -> int:
        """Eski koda hiç yoktu: signals tablosu sınırsız büyürdü.
        retention_days'den eski kayıtları sil, kaç satır silindiğini döndür."""
        cutoff = time.time() - self.retention_days * 86400
        with self._write() as db:
            cur = db.execute("DELETE FROM signals WHERE ts < ?", (cutoff,))
            return cur.rowcount

    def performance_summary(self):
        conn = self._conn()
        return conn.execute("""
            SELECT score, setup, confirmation, max_pct, min_pct, c5, c15,
                   status, entry_quality, priority, d30, d90,
                   trade_1m, trade_5m, market_momentum, trap
            FROM signals WHERE c15 IS NOT NULL
        """).fetchall()
          from __future__ import annotations

import logging
import time
from threading import Lock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings
from rate_limiter import RateLimiter

log = logging.getLogger("balina.client")


def build_session() -> requests.Session:
    retry_args = dict(
        total=2, connect=2, read=2, backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    try:
        retry = Retry(allowed_methods=["GET", "POST"], **retry_args)
    except TypeError:
        retry = Retry(method_whitelist=["GET", "POST"], **retry_args)

    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=40, pool_maxsize=40, max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "BalinaRadari-V23/1.0"})
    return session


class BinanceClient:
    """
    Eski koddaki `api()` fonksiyonu doğrudan `S.request(...)` çağırıyordu --
    hiçbir ağırlık kontrolü yoktu. Burada her çağrı önce RateLimiter'dan
    bütçe talep ediyor; bütçe yoksa istek gönderilmeden bekliyor.

    Ağırlıklar Binance'in genel klines/ticker maliyetlerine yakın kaba
    tahminlerdir (spot API dokümantasyonundaki tipik değerler), tam
    hesap yerine güvenli bir üst sınır olarak kullanılır.
    """

    WEIGHT = {
        "/api/v3/ticker/24hr": 40,   # tüm semboller için tek çağrı, pahalı
        "/api/v3/exchangeInfo": 10,
        "/api/v3/klines": 2,
    }

    def __init__(self, settings: Settings, limiter: RateLimiter):
        self.settings = settings
        self.limiter = limiter
        self.session = build_session()

    def _weight_for(self, path: str) -> int:
        return self.WEIGHT.get(path, 5)

    def api(self, path: str, params: dict | None = None):
        self.limiter.acquire(self._weight_for(path))

        response = self.session.request(
            "GET",
            self.settings.base_url + path,
            params=params,
            timeout=self.settings.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    def tickers(self) -> list:
        try:
            return self.api("/api/v3/ticker/24hr")
        except Exception as e:
            log.error("Ticker: %s", e)
            return []

    def exchange_info(self) -> dict:
        try:
            return self.api("/api/v3/exchangeInfo")
        except Exception as e:
            log.error("ExchangeInfo: %s", e)
            return {}

    def klines(self, symbol: str, interval: str, limit: int) -> list:
        try:
            return self.api("/api/v3/klines", {
                "symbol": symbol, "interval": interval, "limit": limit,
            })
        except Exception as e:
            log.debug("%s %s: %s", symbol, interval, e)
            return []

    def telegram(self, text: str) -> bool:
        if not self.settings.telegram_token or not self.settings.telegram_chat:
            return False
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage",
                json={"chat_id": self.settings.telegram_chat, "text": text},
                timeout=self.settings.request_timeout,
            )
            response.raise_for_status()
            return bool(response.json().get("ok"))
        except Exception as e:
            log.error("Telegram: %s", e)
            return False
      """
Eski koddaki en somut operasyonel risk buradaydı: 80 sembol x 2 kline
çağrısı (~160 istek), 10 thread ile paralel, 30 saniyede bir tekrarlanıyor
-- hiçbir ağırlık/hız kontrolü olmadan. Binance IP başına dakikalık ağırlık
limiti uygular; bunu aşmak IP ban ile sonuçlanabilir (418/429 sonrası
süresi artan banlar).

Bu modül basit bir token-bucket: her istek tahmini bir "ağırlık" tüketir,
bütçe dakikada resetlenir. Bütçe dolduğunda `acquire()` bloklar (kısa
sürelerle) ve thread'i bekletir -- exception fırlatmaz, sadece yavaşlatır.

Not: Binance'in gerçek ağırlık tablosunu birebir yansıtmıyoruz (endpoint'e
göre değişir); amaç kaba ama etkili bir üst sınır koymak. Gerekirse
WEIGHT_BUDGET_PER_MINUTE düşürülüp yükseltilebilir.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, budget_per_minute: int):
        self._budget = budget_per_minute
        self._remaining = budget_per_minute
        self._window_start = time.monotonic()
        self._lock = threading.Lock()

    def _maybe_reset(self) -> None:
        now = time.monotonic()
        if now - self._window_start >= 60:
            self._remaining = self._budget
            self._window_start = now

    def acquire(self, weight: int = 1) -> None:
        """Yeterli bütçe oluşana kadar bloklar, sonra tüketir."""
        while True:
            with self._lock:
                self._maybe_reset()
                if self._remaining >= weight:
                    self._remaining -= weight
                    return
                wait_for = 60 - (time.monotonic() - self._window_start)

            time.sleep(max(0.05, min(wait_for, 2.0)))

    def snapshot(self) -> dict:
        with self._lock:
            self._maybe_reset()
            return {
                "budget": self._budget,
                "remaining": self._remaining,
                "window_age_s": round(time.monotonic() - self._window_start, 1),
}from __future__ import annotations

import time
from threading import Lock

from binance_client import BinanceClient
from config import Settings
from indicators import pct


class MarketData:
    """Eski koddaki DAILY_CACHE / MARKET_CACHE global dict + Lock ikilisinin
    sınıf içine alınmış hali. Davranış aynı, ama artık test için mock'lanabilir
    bir nesne (global state değil)."""

    def __init__(self, client: BinanceClient, cfg: Settings):
        self.client = client
        self.cfg = cfg
        self._daily_cache: dict = {}
        self._daily_lock = Lock()
        self._market_cache: dict = {}
        self._market_lock = Lock()

    def daily_trend(self, symbol: str) -> dict:
        now = time.time()
        with self._daily_lock:
            cached = self._daily_cache.get(symbol)
            if cached:
                ts, data = cached
                if now - ts < self.cfg.daily_cache_ttl:
                    return data

        data = self.client.klines(symbol, "1d", 100)

        if len(data) < 92:
            result = {"ok": False, "d30": 0, "d90": 0}
            with self._daily_lock:
                self._daily_cache[symbol] = (now, result)
            return result

        closed = data[:-1]
        try:
            closes = [float(x[4]) for x in closed]
        except (TypeError, ValueError):
            return {"ok": False, "d30": 0, "d90": 0}

        current = closes[-1]
        result = {
            "ok": True,
            "d30": pct(closes[-31], current),
            "d90": pct(closes[-91], current),
        }

        with self._daily_lock:
            self._daily_cache[symbol] = (now, result)
        return result

    def context(self) -> dict:
        now = time.time()
        with self._market_lock:
            cached = self._market_cache.get(self.cfg.market_symbol)
            if cached:
                ts, data = cached
                if now - ts < self.cfg.daily_cache_ttl:
                    return data

        data = self.client.klines(self.cfg.market_symbol, "5m", 20)

        if len(data) < 5:
            return {"ok": False, "momentum": 0, "state": "VERİ YOK"}

        try:
            closes = [float(x[4]) for x in data[:-1]]
        except (TypeError, ValueError):
            return {"ok": False, "momentum": 0, "state": "VERİ YOK"}

        if len(closes) < 4:
            return {"ok": False, "momentum": 0, "state": "VERİ YOK"}

        momentum = pct(closes[-4], closes[-1])

        if abs(momentum) >= self.cfg.market_move * 2:
            state = "AŞIRI HAREKETLİ"
        elif abs(momentum) >= self.cfg.market_move:
            state = "HAREKETLİ"
        elif momentum > 0.5:
            state = "POZİTİF"
        elif momentum < -0.5:
            state = "NEGATİF"
        else:
            state = "NÖTR"

        result = {"ok": True, "momentum": momentum, "state": state}
        with self._market_lock:
            self._market_cache[self.cfg.market_symbol] = (now, result)
        return result
                 
          """
Saf matematik fonksiyonları: RSI, MACD, ADX, Bollinger Bands, EMA.

Bunların hiçbiri ağ, DB veya global state'e dokunmuyor -> birim testi
yazmak trivial. Eski kodda bu fonksiyonlar da aynıydı ama tek bir 900
satırlık dosyanın içinde kayboluyorlardı; buraya taşımak davranışı
DEĞİŞTİRMEZ, sadece test edilebilir ve tekrar kullanılabilir hale getirir.
"""

from __future__ import annotations


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def pct(a: float, b: float | None) -> float:
    if not a or b is None:
        return 0.0
    return ((b - a) / a) * 100


def clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return avg(values)

    k = 2 / (period + 1)
    result = avg(values[:period])
    for value in values[period:]:
        result = value * k + result * (1 - k)
    return result


def rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0

    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    gain = avg(gains[-period:])
    loss = avg(losses[-period:])

    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def macd(values: list[float]) -> tuple[float, float, float]:
    if len(values) < 35:
        return 0, 0, 0

    values_macd = [
        ema(values[:i], 12) - ema(values[:i], 26)
        for i in range(26, len(values) + 1)
    ]

    main = values_macd[-1]
    signal = ema(values_macd, 9)
    return main, signal, main - signal


def bb(values: list[float], period: int = 20, k: float = 2) -> tuple[float, float, float]:
    if len(values) < period:
        return 0, 0, 0

    sample = values[-period:]
    middle = avg(sample)
    deviation = avg([(x - middle) ** 2 for x in sample]) ** 0.5

    return middle - k * deviation, middle, middle + k * deviation


def adx(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> tuple[float, float, float]:
    if len(closes) < period * 2 + 1:
        return 0, 0, 0

    tr, plus, minus = [], [], []
    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)

    atr = avg(tr[-period:])
    p = avg(plus[-period:])
    m = avg(minus[-period:])

    if atr <= 0:
        return 0, 0, 0

    plus_di = 100 * p / atr
    minus_di = 100 * m / atr
    total = plus_di + minus_di
    dx = 100 * abs(plus_di - minus_di) / total if total else 0

    return dx, plus_di, minus_di
