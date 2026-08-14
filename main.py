from __future__ import annotations
import logging, os, sys, time
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", stream=sys.stdout)
log = logging.getLogger("balina.main")
SETTINGS.validate()
LIMITER = RateLimiter(SETTINGS.weight_budget_per_minute)
CLIENT = BinanceClient(SETTINGS, LIMITER)
DBS = DB(SETTINGS.db_path, retention_days=SETTINGS.signal_retention_days)
MARKET = MarketData(CLIENT, SETTINGS)

def candidates(cfg: Settings, data: list) -> list[dict]:
    result = []
    for ticker in data:
        if not isinstance(ticker, dict): continue
        symbol = str(ticker.get("symbol", ""))
        if not symbol.endswith("TRY") or symbol in cfg.excluded_symbols: continue
        try:
            volume = float(ticker.get("quoteVolume", 0))
            change = float(ticker.get("priceChangePercent", 0))
            price = float(ticker.get("lastPrice", 0))
        except (TypeError, ValueError): continue
        if volume < cfg.min_quote_volume or change > 25 or price <= 0: continue
        result.append({"symbol": symbol, "volume": volume, "chg": change, "price": price})
    return result

def shortlist(cfg: Settings, items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda x: x["volume"] * (1 + max(x["chg"], 0) / 100), reverse=True)[:cfg.shortlist_size]

def message(r: dict) -> str:
    reasons = []
    if r.get("closed_breakout"): reasons.append("Kapanış kırılımı")
    elif r.get("breakout"): reasons.append("Direnç kırıldı")
    elif r.get("dist", 999) <= 0.35: reasons.append(f"Direnç %{r['dist']:.2f}")
    if r.get("vr", 0) >= 1.5: reasons.append(f"1m hacim {r['vr']:.1f}x")
    if r.get("vr5", 0) >= 1.5: reasons.append(f"5m hacim {r['vr5']:.1f}x")
    if r.get("impulse", 0) >= 2: reasons.append(f"İvme {r['impulse']:.1f}x")
    if r.get("bp", 0) >= 65: reasons.append(f"Alıcı %{r['bp']:.0f}")
    if r.get("ema"): reasons.append("EMA trend")
    if r.get("macd"): reasons.append("MACD güçleniyor")
    if r.get("hl"): reasons.append("Higher-Low")
    if r.get("squeeze"): reasons.append("BB sıkışma")
    if r.get("trades_1m", 0) >= SETTINGS.min_1m_trades: reasons.append("İşlem katılımı güçlü")
    status = r.get("status", "BUY")
    title = "🔥 ÇOK GÜÇLÜ AL" if status == "VERY" else "🟢 AL"
    if status == "VERY": result = "🚀 Güçlü teyit."
    elif r.get("closed_breakout"): result = "🎯 Alım teyidi oluştu."
    elif r.get("breakout"): result = "🟢 Kırılım gerçekleşti."
    else: result = "🟡 Kırılım teyidi bekleniyor."
    trap = ""
    if r.get("trap") and r.get("trap_reasons"): trap = "\n⚠️ TUZAK: " + ", ".join(r["trap_reasons"]) + "\n"
    d30 = f"{r['d30']:+.1f}%" if r.get("d30") is not None else "VERİ YOK"
    d90 = f"{r['d90']:+.1f}%" if r.get("d90") is not None else "VERİ YOK"
    return ("🐋 BALİNA RADARI V23\n\n" f"{title}\n\n" f"🪙 #{r['symbol']}\n" f"💰 {r['price']:.8g}\n"
            f"💪 Güç: {r['score']}/100\n" f"🏆 Öncelik: {r['priority']:.0f}/100\n" f"🎯 Giriş: {r['entry_quality']}/100\n"
            f"🔁 Teyit: {r['streak']}x\n\n" f"📊 1m Hacim: {r['vr']:.2f}x | 5m: {r['vr5']:.2f}x\n"
            f"🚀 İvme: {r['impulse']:.2f}x\n" f"🛒 Alıcı: %{r['bp']:.0f}\n" f"🔢 İşlem: {r['trades_1m']}\n"
            f"📈 RSI: {r['rv']:.0f} | ADX: {r['ad']:.0f}\n" f"🎯 Direnç: %{r['dist']:.2f}\n"
            f"🚀 Kırılım: {'✅' if r.get('breakout') else '⏳'}\n" f"📅 30g: {d30} | 90g: {d90}\n"
            f"🌐 BTC/TRY: {r['market_momentum']:+.2f}%\n" f"{trap}\n" f"🔎 {' • '.join(reasons[:8])}\n\n" f"{result}")

def scan() -> bool:
    start = time.time()
    data = CLIENT.tickers()
    if not data: return True
    price_map = {}
    for item in data:
        try:
            symbol, price = item.get("symbol"), float(item.get("lastPrice", 0))
            if symbol and price > 0: price_map[symbol] = price
        except (TypeError, ValueError): continue
    try: DBS.update_outcomes(price_map, SETTINGS.outcome_window)
    except Exception: log.exception("Outcome güncellemesi başarısız")
    all_candidates = candidates(SETTINGS, data)
    items = shortlist(SETTINGS, all_candidates)
    signals, stats = [], {}
    with ThreadPoolExecutor(max_workers=SETTINGS.workers) as executor:
        jobs = [executor.submit(analyze, SETTINGS, CLIENT, DBS, MARKET, item) for item in items]
        for job in as_completed(jobs):
            try: r = job.result()
            except Exception:
                log.exception("Analyze hatası"); r = {"status": "error"}
            status = r.get("status", "error")
            stats[status] = stats.get(status, 0) + 1
            if status in ("BUY", "VERY"): signals.append(r)
    signals = rank_signals(SETTINGS, signals)
    sent = 0
    for r in signals:
        if sent >= SETTINGS.max_signals: break
        if r["priority"] < SETTINGS.min_priority: continue
        if not DBS.can_send(r["symbol"], r["status"], SETTINGS.cooldown): continue
        if CLIENT.telegram(message(r)):
            DBS.put(r["symbol"], r["score"], r["status"], r["status"], sent=time.time(), streak=r["streak"], trap=r["trap"], priority=r["priority"])
            DBS.create_signal(r); sent += 1
        time.sleep(0.3)
    elapsed = time.time() - start
    errors = stats.get("error", 0)
    log.info("V23 | TRY:%d/%d | AL:%d | VERY:%d | Hata:%d | Gönder:%d | %.1fs | budget:%s",
             len(items), len(all_candidates), stats.get("BUY", 0), stats.get("VERY", 0), errors, sent, elapsed, LIMITER.snapshot())
    return errors / max(1, len(items)) > 0.30 or elapsed > SETTINGS.scan_interval * 1.25

def performance() -> dict:
    rows = DBS.performance_summary()
    if not rows: return {"samples": 0, "note": "Henüz tamamlanmış sinyal yok."}
    completed = [r for r in rows if r[6] is not None]
    def stats(data):
        done = [r for r in data if r[6] is not None]
        if not done: return {"samples": len(data), "completed": 0}
        return {"samples": len(data), "completed": len(done), "avg_15m_pct": round(avg([r[6] for r in done]), 2),
                "positive_15m_pct": round(sum(r[6] > 0 for r in done) / len(done) * 100, 1)}
    return {"samples": len(rows), "completed_15m": len(completed),
            "avg_max_pct": round(avg([r[3] for r in rows]), 2), "avg_min_pct": round(avg([r[4] for r in rows]), 2),
            "avg_15m_pct": round(avg([r[6] for r in completed]), 2) if completed else 0,
            "score": {"68_75": stats([r for r in rows if 68 <= r[0] < 76]), "76_83": stats([r for r in rows if 76 <= r[0] < 84]),
                      "84_90": stats([r for r in rows if 84 <= r[0] < 91]), "91_100": stats([r for r in rows if r[0] >= 91])},
            "level": {"BUY": stats([r for r in rows if r[7] == "BUY"]), "VERY": stats([r for r in rows if r[7] == "VERY"])},
            "entry_quality": {"0_49": stats([r for r in rows if r[8] < 50]), "50_69": stats([r for r in rows if 50 <= r[8] < 70]),
                              "70_84": stats([r for r in rows if 70 <= r[8] < 85]), "85_100": stats([r for r in rows if r[8] >= 85])}}

def validate_market() -> None:
    info = CLIENT.exchange_info()
    symbols = {x.get("symbol") for x in info.get("symbols", []) if isinstance(x, dict)}
    try_count = sum(1 for s in symbols if s and s.endswith("TRY"))
    if try_count <= 0: raise RuntimeError(f"BASE {SETTINGS.base_url} üzerinde TRY marketi bulunamadı.")
    if SETTINGS.market_symbol not in symbols: log.warning("%s bulunamadı; BTC filtresi devre dışı.", SETTINGS.market_symbol)
    log.info("V23 | Binance TR doğrulandı | TRY:%d", try_count)

def loop() -> None:
    log.info("🐋 BALİNA RADARI V23 başlatılıyor...")
    try: validate_market()
    except Exception: log.exception("MARKET DOĞRULAMA HATASI"); return
    if SETTINGS.telegram_token and SETTINGS.telegram_chat:
        CLIENT.telegram("🐋 BALİNA RADARI V23 AKTİF\n🏆 Öncelik sistemi aktif\n⚠️ TRAP filtresi aktif\n🛡️ Rate-limit koruması aktif")
    last_cleanup = 0.0
    while True:
        started = time.time()
        try: backoff = scan()
        except Exception: log.exception("Tarama döngüsü hatası"); backoff = True
        if started - last_cleanup > 86400:
            try:
                removed = DBS.cleanup_old_signals()
                if removed: log.info("Retention: %d eski sinyal silindi.", removed)
            except Exception: log.exception("Retention temizliği başarısız")
            last_cleanup = started
        elapsed = time.time() - started
        time.sleep(max(180, SETTINGS.scan_interval * 3) if backoff else max(1, SETTINGS.scan_interval - elapsed))

app = Flask(__name__)

def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if SETTINGS.admin_token and request.headers.get("X-Admin-Token") != SETTINGS.admin_token: abort(401)
        return fn(*args, **kwargs)
    return wrapper

@app.route("/")
def home(): return "🐋 BALİNA RADARI V23 AKTİF"

@app.route("/health")
def health():
    return {"status": "ok", "bot": "Balina Radarı V23", "base": SETTINGS.base_url,
            "scan_interval": SETTINGS.scan_interval, "workers": SETTINGS.workers, "rate_limit": LIMITER.snapshot()}

@app.route("/performance")
@require_admin
def performance_route(): return performance()

Thread(target=loop, daemon=True, name="balina-v23").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), use_reloader=False)
