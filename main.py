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
          
