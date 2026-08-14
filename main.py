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

SETTINGS.validate()

if not SETTINGS.admin_token:
    log.warning("ADMIN_TOKEN ayarlanmamış -- /performance korumasız.")

LIMITER = RateLimiter(SETTINGS.weight_budget_per_minute)
CLIENT = BinanceClient(SETTINGS, LIMITER)
DBS = DB(SETTINGS.db_path, retention_days=SETTINGS.signal_retention_days)
MARKET = MarketData(CLIENT, SETTINGS)


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
    def rank(item: dict) -> float:
        return item["volume"] * (1 + max(item["chg"], 0) / 100)
    return sorted(items, key=rank, reverse=True)[:cfg.shortlist_size]


def phase_title(r: dict) -> str:
    phase = r.get("phase", "")
    if phase == "PRE_BREAKOUT":
        return "🟡 ÖNCÜ AL"
    if phase == "STRONG":
        return "🚀 GÜÇLÜ AL"
    return "🟢 AL"


def history_text(r: dict) -> str:
    count = int(r.get("history_count", 0))
    signal_number = count + 1
    lines = [
        f"🔢 Sinyal: #{signal_number}",
        f"📨 Daha önce: {count} sinyal",
    ]
    change = r.get("price_change_since_last")
    if count > 0 and change is not None:
        lines.append(f"📈 Son sinyalden: {change:+.2f}%")
        if change > 0.5:
            lines.append("🚀 Önceki sinyalden sonra hareket devam etti")
        elif change < -0.5:
            lines.append("⚠️ Önceki sinyalden sonra fiyat geri çekildi")
        else:
            lines.append("⏸️ Önceki sinyalden sonra fiyat yatay kaldı")
    return "\n".join(lines)


def message(r: dict) -> str:
    title = phase_title(r)
    reasons = []

    if r.get("pre_breakout"):
        reasons.append("Öncü hareket")
    if r.get("closed_breakout"):
        reasons.append("Kapanış kırılımı")
    elif r.get("breakout"):
        reasons.append("Direnç kırıldı")
    elif r.get("dist", 999) <= 0.70:
        reasons.append(f"Direnç %{r['dist']:.2f}")

    if r.get("vr", 0) >= 1.5:
        reasons.append(f"1m hacim {r['vr']:.1f}x")
    if r.get("vr5", 0) >= 1.5:
        reasons.append(f"5m hacim {r['vr5']:.1f}x")
    if r.get("impulse", 0) >= 1.5:
        reasons.append(f"İvme {r['impulse']:.1f}x")
    if r.get("bp", 0) >= 65:
        reasons.append(f"Alıcı %{r['bp']:.0f}")
    if r.get("ema"):
        reasons.append("EMA trend")
    if r.get("macd"):
        reasons.append("MACD güçleniyor")
    if r.get("hl"):
        reasons.append("Higher-Low")
    if r.get("squeeze"):
        reasons.append("BB sıkışma")
    if r.get("trades_1m", 0) >= SETTINGS.min_1m_trades:
        reasons.append("İşlem katılımı güçlü")
    if r.get("volume_building"):
        reasons.append("Hacim oluşuyor")
    if r.get("pressure_building"):
        reasons.append("Alıcı baskısı")
    if r.get("momentum_building"):
        reasons.append("Momentum oluşuyor")

    trap = ""
    if r.get("trap"):
        trap = "\n⚠️ TUZAK: " + ", ".join(r.get("trap_reasons", [])) + "\n"

    phase = r.get("phase", "")
    if phase == "STRONG":
        result = "🚀 Güçlü hareket teyidi."
    elif phase == "CONFIRMED":
        result = "🎯 Alım teyidi güçlendi."
    elif phase == "PRE_BREAKOUT":
        result = "👀 Hareket erken aşamada. Kırılım henüz şart değil."
    elif r.get("closed_breakout"):
        result = "🎯 Alım teyidi oluştu."
    else:
        result = "🟡 Hareket güçleniyor."

    d30 = f"{r['d30']:+.1f}%" if r.get("d30") is not None else "VERİ YOK"
    d90 = f"{r['d90']:+.1f}%" if r.get("d90") is not None else "VERİ YOK"

    return (
        "🐋 BALİNA RADARI V24\n\n"
        f"{title}\n\n"
        f"🪙 #{r['symbol']}\n"
        f"💰 {r['price']:.8g}\n\n"
        f"💪 Güç: {r['score']}/100\n"
        f"🏆 Öncelik: {r['priority']:.0f}/100\n"
        f"🎯 Giriş: {r['entry_quality']}/100\n"
        f"🧩 Güçlü grup: {r.get('group_count', 0)}/6\n\n"
        f"{history_text(r)}\n\n"
        f"📊 1m Hacim: {r['vr']:.2f}x | 5m: {r['vr5']:.2f}x\n"
        f"🚀 İvme: {r['impulse']:.2f}x\n"
        f"🛒 Alıcı: %{r['bp']:.0f}\n"
        f"🔢 İşlem: {r['trades_1m']}\n"
        f"📈 RSI: {r['rv']:.0f} | ADX: {r['ad']:.0f}\n"
        f"🎯 Direnç: %{r['dist']:.2f}\n"
        f"🚀 Kırılım: {'✅' if r.get('breakout') else '⏳'}\n"
        f"📅 30g: {d30} | 90g: {d90}\n"
        f"🌐 BTC/TRY: {r['market_momentum']:+.2f}%\n"
        f"{trap}\n"
        f"🔎 {' • '.join(reasons[:10])}\n\n"
        f"{result}"
    )


def enrich_history(r: dict) -> dict:
    try:
        history = DBS.signal_history(r["symbol"])
    except Exception:
        log.exception("%s sinyal geçmişi okunamadı", r.get("symbol"))
        r["history_count"] = 0
        r["price_change_since_last"] = None
        return r

    count = int(history.get("count", 0))
    last_price = history.get("last_price")
    r["history_count"] = count

    if last_price is not None and last_price > 0:
        r["price_change_since_last"] = (
            (r["price"] - last_price) / last_price * 100
        )
    else:
        r["price_change_since_last"] = None
    return r


def scan() -> bool:
    start = time.time()
    data = CLIENT.tickers()
    if not data:
        log.warning("Ticker verisi alınamadı.")
        return True

    price_map = {}
    for item in data:
        try:
            symbol = item.get("symbol")
            price = float(item.get("lastPrice", 0))
            if symbol:
                price_map[symbol] = price
        except (TypeError, ValueError):
            continue

    try:
        DBS.update_outcomes(price_map, SETTINGS.outcome_window)
    except Exception:
        log.exception("Outcome güncellemesi başarısız")

    all_candidates = candidates(SETTINGS, data)
    items = shortlist(SETTINGS, all_candidates)

    signals = []
    stats = {}

    with ThreadPoolExecutor(max_workers=SETTINGS.workers) as executor:
        jobs = [
            executor.submit(analyze, SETTINGS, CLIENT, DBS, MARKET, item)
            for item in items
        ]
        for job in as_completed(jobs):
            try:
                r = job.result()
            except Exception:
                log.exception("Analyze job hatası")
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
        if r.get("priority", 0) < SETTINGS.min_priority:
            continue
        if not DBS.can_send(r["symbol"], r["status"], SETTINGS.cooldown):
            continue

        r = enrich_history(r)

        if CLIENT.telegram(message(r)):
            now = time.time()
            try:
                DBS.put(
                    r["symbol"],
                    r["score"],
                    r["status"],
                    r.get("phase", r["status"]),
                    sent=now,
                    streak=r.get("streak", 0),
                    trap=r.get("trap", False),
                    priority=r.get("priority", 0),
                )
                DBS.create_signal(r)
                sent += 1
                log.info(
                    "Sinyal gönderildi | %s | phase=%s | signal_no=%d | score=%s | priority=%s",
                    r["symbol"],
                    r.get("phase", "NONE"),
                    r.get("history_count", 0) + 1,
                    r.get("score", 0),
                    r.get("priority", 0),
                )
            except Exception:
                log.exception("%s sinyal DB'ye yazılamadı", r["symbol"])

        time.sleep(0.3)

    elapsed = time.time() - start
    errors = stats.get("error", 0)

    log.info(
        "V24 | TRY:%d/%d | AL:%d | VERY:%d | ÖNCÜ:%d | Hata:%d | Gönder:%d | %.1fs | budget:%s",
        len(items),
        len(all_candidates),
        stats.get("BUY", 0),
        stats.get("VERY", 0),
        sum(1 for x in signals if x.get("phase") == "PRE_BREAKOUT"),
        errors,
        sent,
        elapsed,
        LIMITER.snapshot(),
    )

    return (
        errors / max(1, len(items)) > 0.30
        or elapsed > SETTINGS.scan_interval * 1.25
    )


def performance() -> dict:
    rows = DBS.performance_summary()
    if not rows:
        return {"samples": 0, "note": "Henüz tamamlanmış sinyal yok."}

    completed = [r for r in rows if r[6] is not None]

    def stats(data: list) -> dict:
        done = [r for r in data if r[6] is not None]
        if not done:
            return {"samples": len(data), "completed": 0}
        return {
            "samples": len(data),
            "completed": len(done),
            "avg_15m_pct": round(avg([r[6] for r in done]), 2),
            "positive_15m_pct": round(
                sum(r[6] > 0 for r in done) / len(done) * 100, 1
            ),
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
        raise RuntimeError(
            f"BASE {SETTINGS.base_url} üzerinde TRY marketi bulunamadı."
        )

    if SETTINGS.market_symbol not in symbols:
        log.warning("%s bulunamadı; BTC filtresi devre dışı.", SETTINGS.market_symbol)

    log.info("V24 | Binance TR doğrulandı | TRY:%d", try_count)


def loop() -> None:
    log.info("🐋 BALİNA RADARI V24 başlatılıyor...")

    try:
        validate_market()
    except Exception as e:
        log.exception("MARKET DOĞRULAMA HATASI: %s", e)
        return

    if SETTINGS.telegram_token and SETTINGS.telegram_chat:
        CLIENT.telegram(
            "🐋 BALİNA RADARI V24 AKTİF\n"
            "🟡 Öncü AL sistemi aktif\n"
            "🟢 Çok aşamalı karar motoru aktif\n"
            "📊 Hacim + işlem + alıcı + EMA + MACD + RSI + momentum\n"
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


app = Flask(__name__)


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if SETTINGS.admin_token:
            if request.headers.get("X-Admin-Token") != SETTINGS.admin_token:
                abort(401)
        return fn(*args, **kwargs)
    return wrapper


@app.route("/")
def home():
    return "🐋 Balina Radarı V24 Aktif"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina Radarı V24",
        "base": SETTINGS.base_url,
        "scan_interval": SETTINGS.scan_interval,
        "workers": SETTINGS.workers,
        "rate_limit": LIMITER.snapshot(),
    }


@app.route("/performance")
@require_admin
def performance_route():
    return performance()


Thread(target=loop, daemon=True, name="balina-v24").start()


if __name__ == "__main__":
    import os
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        use_reloader=False,
    )
    
