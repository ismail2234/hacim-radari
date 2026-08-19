from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread

from flask import Flask

from binance_client import BinanceClient
from config import SETTINGS
from db import DB
from market import MarketData
from scoring import analyze, rank_signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("balina.v28")

SETTINGS.validate()

CLIENT = BinanceClient(SETTINGS)
DBS = DB(
    SETTINGS.db_path,
    SETTINGS.signal_retention_days,
)
MARKET = MarketData(CLIENT, SETTINGS)

BAD_SYMBOLS = {
    "USDTTRY",
    "USDCTRY",
    "BUSDTRY",
    "FDUSDTRY",
    "TUSDTRY",
    "DAITRY",
}


def candidates(data):
    result = []

    for ticker in data:
        symbol = str(ticker.get("symbol", "")).upper()

        if not symbol.endswith("TRY"):
            continue
        if symbol in BAD_SYMBOLS:
            continue
        if symbol in SETTINGS.excluded_symbols:
            continue

        try:
            volume = float(ticker.get("quoteVolume", 0))
            change = float(ticker.get("priceChangePercent", 0))
            price = float(ticker.get("lastPrice", 0))
        except (TypeError, ValueError):
            continue

        if price <= 0:
            continue
        if volume < SETTINGS.min_quote_volume:
            continue
        if change > 25:
            continue

        result.append({
            "symbol": symbol,
            "volume": volume,
            "chg": change,
            "price": price,
        })

    result.sort(
        key=lambda x: x["volume"] * (1 + max(x["chg"], 0) / 100),
        reverse=True,
    )
    return result[:SETTINGS.shortlist_size]


def analyze_one(item):
    try:
        return analyze(
            SETTINGS,
            CLIENT,
            DBS,
            MARKET,
            item,
        )
    except Exception as e:
        log.exception(
            "Analiz hatasÄ± | %s | %s",
            item.get("symbol", "?"),
            e,
        )
        return None


def telegram_message(r):
    status = r.get("status", "PASS")

    if status == "VERY":
        title = "ğŸš€ GÃœÃ‡LÃœ AL"
    elif status == "BUY":
        title = "ğŸŸ¢ AL"
    else:
        title = "ğŸŸ¡ Ã–NCÃœ"

    streak = int(r.get("streak", 1) or 1)

    if streak >= 3:
        confirm = f"ğŸ”¥ {streak}. TEYÄ°T"
    elif streak == 2:
        confirm = "âœ… 2. TEYÄ°T"
    else:
        confirm = "ğŸ” 1. TEYÄ°T"

    td = int(r.get("td_setup", r.get("td", 0)) or 0)

    if td >= 13:
        td_text = "13 ğŸ”¥"
    elif td >= 9:
        td_text = "9ï¸âƒ£ 9"
    else:
        td_text = str(td)

    criteria = r.get("criteria_list", r.get("criteria", []))
    if isinstance(criteria, (list, tuple)):
        criteria_text = ", ".join(str(x) for x in criteria)
    else:
        criteria_text = str(criteria)

    stage = str(r.get("kivrim_stage", "BEKLE"))

    return (
        "ğŸ‹ BALÄ°NA RADARI V28 KIVRIM\n\n"
        f"{title} | {confirm}\n"
        f"ğŸª™ #{r.get('symbol', '?')}\n"
        f"ğŸ’° {r.get('price', 0):.8g}\n"
        f"ğŸ¯ Skor: {r.get('score', 0):.0f}/100\n"
        f"ğŸŒ€ KÄ±vrÄ±m: {stage}\n"
        f"ğŸ¯ KÄ±vrÄ±m Skoru: {r.get('kivrim_score', 0):.0f}/100\n"
        f"âš¡ KÄ±vrÄ±m Erkenlik: {r.get('kivrim_early_score', 0):.0f}/100\n"
        f"â†ªï¸ EMA7 KÄ±vrÄ±m: {'âœ…' if r.get('kivrim_turning', False) else 'âŒ'}\n"
        f"ğŸ“‰ Higher-Low: {'âœ…' if r.get('kivrim_higher_low', False) else 'âŒ'}\n\n"
        "â˜ï¸ ICHIMOKU\n"
        f"Trend: {'YÃœKSELÄ°Å âœ…' if r.get('ichimoku_bullish', r.get('bullish', False)) else 'ZAYIF'}\n"
        f"Bulut: {'ÃœSTÃœNDE âœ…' if r.get('above_cloud', False) else 'ALTINDA âŒ'}\n\n"
        "ğŸ“ FIB\n"
        f"0.5: {r.get('fib_0_5', r.get('fib50', 0)):.8g}\n"
        f"0.618: {r.get('fib_0_618', r.get('fib618', 0)):.8g}\n"
        f"0.786: {r.get('fib_0_786', r.get('fib786', 0)):.8g}\n"
        f"BÃ¶lge: {'âœ…' if r.get('fib_zone', False) else 'â³'}\n\n"
        "ğŸ“Š VOLUME PROFILE\n"
        f"POC: {r.get('poc', 0):.8g}\n"
        f"VA: {r.get('va_low', r.get('value_low', 0)):.8g}"
        f" - {r.get('va_high', r.get('value_high', 0)):.8g}\n"
        f"Fib + POC: {'ğŸ”¥ KESÄ°ÅÄ°M' if r.get('fib_poc', False) else 'â³'}\n\n"
        f"9ï¸âƒ£ TD: {td_text}\n\n"
        f"ğŸ“Š Hacim: {r.get('volume_ratio', r.get('vr', 0)):.2f}x\n"
        f"ğŸ“ˆ RSI: {r.get('rsi', r.get('rv', 0)):.1f}\n"
        f"ğŸ“ˆ MACD: {'âœ…' if r.get('macd', False) else 'âŒ'}\n"
        f"ğŸ“ˆ ADX: {r.get('adx', r.get('ad', 0)):.1f}\n"
        f"ğŸ’  VWAP: {'âœ…' if r.get('price_above_vwap', False) else 'âŒ'}\n\n"
        f"ğŸ›‘ Stop: {r.get('stop_loss', r.get('stop', 0)):.8g}\n"
        f"ğŸ“‰ Risk: %{r.get('stop_distance', 0):.2f}\n\n"
        f"ğŸ¯ Teyitler: {criteria_text}\n"
        f"ğŸŒ€ KÄ±vrÄ±m Detay: {r.get('kivrim_reasons_text', '')}\n\n"
        "âš ï¸ YatÄ±rÄ±m tavsiyesi deÄŸildir."
    )


def scan():
    started = time.time()

    try:
        data = CLIENT.tickers()
        if not data:
            log.warning("Ticker verisi yok.")
            return False

        items = candidates(data)

        if not items:
            log.info("Uygun TRY coin yok.")
            return False

        log.info("V28 tarama baÅŸladÄ± | aday:%d", len(items))

        results = []

        with ThreadPoolExecutor(max_workers=SETTINGS.workers) as executor:
            futures = {
                executor.submit(analyze_one, item): item
                for item in items
            }

            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    item = futures[future]
                    log.exception(
                        "Worker hatasÄ± | %s | %s",
                        item.get("symbol", "?"),
                        e,
                    )

        signals = [
            x for x in results
            if x.get("status") in ("ONCU", "BUY", "VERY")
        ]
                if not signals:
            log.info("Sinyal yok.")
            return False

        try:
            ranked = rank_signals(signals, SETTINGS)
        except TypeError:
            try:
                ranked = rank_signals(signals)
            except Exception:
                log.exception("rank_signals hatasÄ±")
                ranked = signals
        except Exception:
            log.exception("rank_signals hatasÄ±")
            ranked = signals

        ranked = ranked[:SETTINGS.max_signals]
        sent = 0

        for result in ranked:
            symbol = result.get("symbol", "")
            level = result.get("status", "PASS")

            if level == "PASS":
                continue

            try:
                can_send = DBS.can_send(
                    symbol,
                    level,
                    SETTINGS.cooldown,
                )
            except Exception:
                log.exception("Cooldown hatasÄ± | %s", symbol)
                can_send = False

            if not can_send:
                log.info("Cooldown | %s | %s", symbol, level)
                continue

            try:
                message_id = CLIENT.telegram(telegram_message(result))
            except Exception:
                log.exception("Telegram hatasÄ± | %s", symbol)
                message_id = False

            if not message_id:
                continue

            sent += 1

            try:
                DBS.create_signal(result)
            except Exception:
                log.exception("DB sinyal kaydÄ± | %s", symbol)

            try:
                DBS.put(
                    symbol=symbol,
                    score=result.get("score", 0),
                    level=level,
                    stage=level,
                    sent=True,
                    streak=result.get("streak", 1),
                    trap=result.get("trap", False),
                    priority=result.get("priority", 0),
                )
            except Exception:
                log.exception("DB state kaydÄ± | %s", symbol)

            log.info(
                "V28 SÄ°NYAL | %s | %s | skor=%s | kivrim=%s | kivrim_early=%s",
                symbol,
                level,
                result.get("score", 0),
                result.get("kivrim_score", 0),
                result.get("kivrim_early_score", 0),
            )

        try:
            price_map = {
                str(x.get("symbol", "")).upper():
                float(x.get("lastPrice", 0))
                for x in data
                if x.get("symbol")
            }
            DBS.update_outcomes(
                price_map,
                SETTINGS.outcome_window,
            )
        except Exception:
            log.exception("Outcome hatasÄ±")

        elapsed = time.time() - started

        log.info(
            "V28 | aday:%d | sonuÃ§:%d | sinyal:%d | gÃ¶nder:%d | %.1fs",
            len(items),
            len(results),
            len(signals),
            sent,
            elapsed,
        )

        return elapsed > SETTINGS.scan_interval * 1.25

    except Exception:
        log.exception("SCAN genel hatasÄ±")
        return True


def loop():
    log.info("ğŸ‹ BALÄ°NA RADARI V28 KIVRIM BAÅLADI")

    try:
        info = CLIENT.exchange_info()
        symbols = {
            str(x.get("symbol", "")).upper()
            for x in info.get("symbols", [])
            if x.get("symbol")
        }

        try_count = sum(
            1 for x in symbols
            if x.endswith("TRY") and x not in BAD_SYMBOLS
        )

        log.info("Binance doÄŸrulandÄ± | TRY:%d", try_count)
    except Exception:
        log.exception("Binance kontrol hatasÄ±")

    if SETTINGS.telegram_token and SETTINGS.telegram_chat:
        try:
            CLIENT.telegram(
                "ğŸ‹ BALÄ°NA RADARI V28 KIVRIM AKTÄ°F\n"
                "KÄ±vrÄ±m + EMA7 + Dip + Higher-Low + "
                "RSI + MACD + Hacim + Ichimoku + "
                "Fibonacci + Volume Profile"
            )
        except Exception:
            log.exception("Telegram aktivasyon hatasÄ±")

    last_cleanup = 0.0

    while True:
        started = time.time()

        try:
            backoff = scan()
        except Exception:
            log.exception("Loop hatasÄ±")
            backoff = True

        if time.time() - last_cleanup > 86400:
            try:
                removed = DBS.cleanup_old_signals()
                if removed:
                    log.info("Temizlenen sinyal: %d", removed)
            except Exception:
                log.exception("DB temizlik hatasÄ±")
            last_cleanup = time.time()

        elapsed = time.time() - started

        if backoff:
            wait = max(180, SETTINGS.scan_interval * 3)
        else:
            wait = max(1, SETTINGS.scan_interval - elapsed)

        log.info("Sonraki tarama: %.0f sn", wait)
        time.sleep(wait)


app = Flask(__name__)


@app.route("/")
def home():
    return "ğŸ‹ Balina RadarÄ± V28 KÄ±vrÄ±m Aktif"


@app.route("/health")
def health():
    return {
        "status": "ok",
        "bot": "Balina RadarÄ± V28 KÄ±vrÄ±m",
        "scan_interval": SETTINGS.scan_interval,
        "workers": SETTINGS.workers,
    }


@app.route("/performance")
def performance():
    try:
        data = DBS.performance_summary()
        return {
            "status": "ok",
            "samples": len(data),
        }
    except Exception:
        return {
            "status": "ok",
            "samples": 0,
        }


Thread(
    target=loop,
    daemon=True,
    name="balina-v28",
).start()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
    )
