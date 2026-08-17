from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread

from flask import Flask

from binance_client import BinanceClient
from config import SETTINGS, Settings
from db import DB
from market import MarketData
from rate_limiter import RateLimiter
from scoring import analyze, rank_signals


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)

log = logging.getLogger("balina.main")


# ============================================================
# AYARLAR
# ============================================================

SETTINGS.validate()


# ============================================================
# ANA NESNELER
# ============================================================

LIMITER = RateLimiter(
    SETTINGS.weight_budget_per_minute
)

CLIENT = BinanceClient(
    SETTINGS,
    LIMITER,
)

DBS = DB(
    SETTINGS.db_path,
    SETTINGS.signal_retention_days,
)

MARKET = MarketData(
    CLIENT,
    SETTINGS,
)


# ============================================================
# ADAYLAR
# ============================================================

def candidates(
    cfg: Settings,
    data: list,
) -> list[dict]:

    result = []

    for ticker in data:

        symbol = str(
            ticker.get("symbol", "")
        ).upper()

        if (
            not symbol.endswith("TRY")
            or symbol in cfg.excluded_symbols
        ):
            continue

        try:
            volume = float(
                ticker.get(
                    "quoteVolume",
                    0,
                )
            )

            change = float(
                ticker.get(
                    "priceChangePercent",
                    0,
                )
            )

            price = float(
                ticker.get(
                    "lastPrice",
                    0,
                )
            )

            if (
                volume < cfg.min_quote_volume
                or change > 25
                or price <= 0
            ):
                continue

            result.append(
                {
                    "symbol": symbol,
                    "volume": volume,
                    "chg": change,
                    "price": price,
                }
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

    return result


# ============================================================
# SHORTLIST
# ============================================================

def shortlist(
    cfg: Settings,
    items: list[dict],
) -> list[dict]:

    def ranking(item: dict) -> float:

        volume = float(
            item.get("volume", 0)
        )

        change = max(
            float(
                item.get("chg", 0)
            ),
            0.0,
        )

        return volume * (
            1.0 + change / 100.0
        )

    return sorted(
        items,
        key=ranking,
        reverse=True,
    )[:cfg.shortlist_size]


# ============================================================
# TELEGRAM MESAJI
# ============================================================


def message(r: dict) -> str:

    titles = {
        "ONCU": "🟡 ÖNCÜ AL",
        "BUY": "🟢 AL",
        "VERY": "🚀 GÜÇLÜ AL",
    }

    status = r.get("status", "PASS")
    title = titles.get(status, status)

    try:
        streak = max(
            1,
            int(r.get("streak", 1) or 1),
        )
    except (TypeError, ValueError):
        streak = 1

    if streak == 1:
        teyit = "🔁 1. TEYİT"
    elif streak == 2:
        teyit = "🔁 2. TEYİT ✅"
    elif streak == 3:
        teyit = "🔁 3. TEYİT 🔥"
    else:
        teyit = f"🔁 {streak}. TEYİT 🔥"

    d30 = r.get("d30")
    d90 = r.get("d90")

    d30_text = (
        f"{d30:+.1f}%"
        if d30 is not None
        else "-"
    )

    d90_text = (
        f"{d90:+.1f}%"
        if d90 is not None
        else "-"
    )

    return (
        "🐋 BALİNA RADARI V26\n\n"

        f"{title} {streak}x\n"
        f"🪙 #{r.get('symbol', '?')}  "
        f"💰 {r.get('price', 0):.8g}\n\n"

        f"💪 Skor: {r.get('score', 0):.0f}/100  "
        f"🏆 Öncelik: {r.get('priority', 0):.0f}\n"

        f"🎯 Giriş: "
        f"{r.get('entry_quality', 0):.0f}\n\n"

        f"📦 Daralma: "
        f"{'✅' if r.get('consolidation') else '❌'} "
        f"%{r.get('consolidation_range', 0):.2f}\n"

        f"🚀 Kırılım: "
        f"{'✅' if r.get('closed_breakout') else '⏳'}  "
        f"Direnç: %{r.get('dist', 0):.2f}\n\n"

        f"📊 Hacim: {r.get('vr', 0):.2f}x  "
        f"5m: {r.get('vr5', 0):.2f}x\n"

        f"⚡ İvme: {r.get('impulse', 0):.2f}x  "
        f"🟢 Alıcı: %{r.get('bp', 0):.0f}\n\n"

        f"📈 RSI: {r.get('rv', 0):.1f}  "
        f"ADX: {r.get('ad', 0):.1f} "
        f"{'↗️' if r.get('adx_rising') else '↘️'}\n"

        f"📈 MACD: "
        f"{'✅' if r.get('macd') else '❌'}  "

        f"💠 VWAP: "
        f"{'✅' if r.get('price_above_vwap') else '❌'}\n\n"

        f"🛑 Stop: "
        f"{r.get('stop_loss', 0):.8g} "
        f"(%{r.get('stop_distance', 0):.2f})\n\n"

        f"{teyit}\n"

        f"📅 30g: {d30_text} | "
        f"90g: {d90_text}\n"

        f"🌐 BTC/TRY: "
        f"{r.get('market_momentum', 0):+.2f}%\n\n"

        "⚠️ Yatırım tavsiyesi değildir."
    )
# ============================================================
# TEK COIN ANALİZİ
# ============================================================

def analyze_one(item: dict) -> dict:

    try:
        result = analyze(
            SETTINGS,
            CLIENT,
            DBS,
            MARKET,
            item,
        )

        return result or {
            "symbol": item.get(
                "symbol",
                "?",
            ),
            "status": "PASS",
        }

    except Exception as e:

        log.exception(
            "Analiz hatası | %s | %s",
            item.get("symbol", "?"),
            e,
        )

        return {
            "symbol": item.get(
                "symbol",
                "?",
            ),
            "status": "PASS",
            "error": str(e),
        }


# ============================================================
# TARAMA
# ============================================================

def scan() -> bool:

    started = time.time()

    stats = {
        "ONCU": 0,
        "BUY": 0,
        "VERY": 0,
        "PASS": 0,
        "error": 0,
    }

    sent = 0

    try:

        all_candidates = CLIENT.tickers()

        if not all_candidates:
            log.warning(
                "Ticker verisi alınamadı."
            )
            return True

        items = candidates(
            SETTINGS,
            all_candidates,
        )

        if not items:
            log.info(
                "Uygun TRY coin bulunamadı."
            )
            return False

        items = shortlist(
            SETTINGS,
            items,
        )

        try_count = sum(
            1
            for x in all_candidates
            if str(
                x.get(
                    "symbol",
                    "",
                )
            ).upper().endswith("TRY")
        )

        log.info(
            "Tarama başladı | "
            "TRY:%d | Shortlist:%d",
            try_count,
            len(items),
        )

        results = []

        with ThreadPoolExecutor(
            max_workers=SETTINGS.workers,
        ) as executor:

            futures = {
                executor.submit(
                    analyze_one,
                    item,
                ): item
                for item in items
            }

            for future in as_completed(
                futures
            ):

                item = futures[future]

                try:

                    result = future.result()

                    if result:
                        results.append(
                            result
                        )

                except Exception as e:

                    stats["error"] += 1

                    log.exception(
                        "Worker hatası | %s | %s",
                        item.get(
                            "symbol",
                            "?",
                        ),
                        e,
                    )
        # ----------------------------------------------------
        # SONUÇLARI SAY
        # ----------------------------------------------------

        for result in results:

            status = result.get(
                "status",
                "PASS",
            )

            if status in stats:
                stats[status] += 1

        # ----------------------------------------------------
        # SADECE GERÇEK SİNYALLER
        # ----------------------------------------------------

        signals = [
            r
            for r in results
            if r.get("status")
            in (
                "ONCU",
                "BUY",
                "VERY",
            )
        ]

        # ----------------------------------------------------
        # SIRALAMA
        # ----------------------------------------------------

        if signals:

            try:
                ranked = rank_signals(
                    signals,
                    SETTINGS,
                )

            except TypeError:

                try:
                    ranked = rank_signals(
                        signals
                    )

                except Exception:
                    log.exception(
                        "rank_signals hatası"
                    )
                    ranked = signals

            except Exception:

                log.exception(
                    "rank_signals hatası"
                )

                ranked = signals

        else:

            ranked = []

        # ----------------------------------------------------
        # MAX SİNYAL
        # ----------------------------------------------------

        ranked = ranked[
            :SETTINGS.max_signals
        ]

        # ----------------------------------------------------
        # TELEGRAM
        # ----------------------------------------------------

        for result in ranked:

            symbol = result.get(
                "symbol",
                "",
            )

            level = result.get(
                "status",
                "PASS",
            )

            if level == "PASS":
                continue

            try:

                can_send = DBS.can_send(
                    symbol,
                    level,
                    SETTINGS.cooldown,
                )

            except Exception:

                log.exception(
                    "can_send hatası | %s",
                    symbol,
                )

                can_send = False

            if not can_send:

                log.info(
                    "Cooldown | %s | %s",
                    symbol,
                    level,
                )

                continue

            text = message(result)

            try:

                message_id = CLIENT.telegram(
                    text
                )

            except Exception:

                log.exception(
                    "Telegram gönderim hatası | %s",
                    symbol,
                )

                message_id = None

            if not message_id:
                continue

            sent += 1

            # ------------------------------------------------
            # DB SİNYAL KAYDI
            # ------------------------------------------------

            try:

                DBS.create_signal(
                    result
                )

            except Exception:

                log.exception(
                    "Sinyal DB kaydı başarısız | %s",
                    symbol,
                )

            # ------------------------------------------------
            # STATE
            # ------------------------------------------------

            try:

                DBS.put(
                    symbol=symbol,
                    score=result.get(
                        "score",
                        0,
                    ),
                    level=level,
                    stage=level,
                    sent=True,
                    streak=result.get(
                        "streak"
                    ),
                    trap=result.get(
                        "trap",
                        False,
                    ),
                    priority=result.get(
                        "priority",
                        0,
                    ),
                )

            except Exception:

                log.exception(
                    "State kaydı başarısız | %s",
                    symbol,
                )

            log.info(
                "SİNYAL GÖNDERİLDİ | "
                "%s | %s | skor=%s",
                symbol,
                level,
                result.get(
                    "score",
                    0,
                ),
            )

        # ----------------------------------------------------
        # OUTCOME
        # ----------------------------------------------------

        try:

            price_map = {
                str(
                    x.get(
                        "symbol"
                    )
                ).upper(): float(
                    x.get(
                        "lastPrice",
                        0,
                    )
                )
                for x in all_candidates
                if x.get("symbol")
            }

            DBS.update_outcomes(
                price_map,
                SETTINGS.outcome_window,
            )

        except Exception:

            log.exception(
                "Outcome güncelleme hatası"
            )

        # ----------------------------------------------------
        # SÜRE
        # ----------------------------------------------------

        elapsed = (
            time.time()
            - started
        )

        log.info(
            "V26 | "
            "TRY:%d/%d | "
            "ÖNCÜ:%d | "
            "AL:%d | "
            "VERY:%d | "
            "Hata:%d | "
            "Gönder:%d | "
            "%.1fs",
            len(items),
            len(all_candidates),
            stats.get(
                "ONCU",
                0,
            ),
            stats.get(
                "BUY",
                0,
            ),
            stats.get(
                "VERY",
                0,
            ),
            stats.get(
                "error",
                0,
            ),
            sent,
            elapsed,
        )

        # ----------------------------------------------------
        # BACKOFF
        # ----------------------------------------------------

        return (
            stats.get(
                "error",
                0,
            )
            / max(
                1,
                len(items),
            )
            > 0.30
            or elapsed
            > SETTINGS.scan_interval * 1.25
        )

    except Exception:

        log.exception(
            "SCAN genel hatası"
        )

        return True


# ============================================================
# ANA LOOP
# ============================================================

def loop():

    log.info(
        "🐋 BALİNA RADARI V26 başlatılıyor..."
    )

    # --------------------------------------------------------
    # BINANCE MARKET KONTROLÜ
    # --------------------------------------------------------

    try:

        info = CLIENT.exchange_info()

        if not info:
            raise RuntimeError(
                "Binance exchangeInfo boş döndü."
            )

        symbols = {
            str(
                x.get(
                    "symbol",
                    "",
                )
            ).upper()
            for x in info.get(
                "symbols",
                [],
            )
            if x.get("symbol")
        }

        try_count = sum(
            1
            for symbol in symbols
            if symbol.endswith("TRY")
        )

        if try_count <= 0:

            log.error(
                "TRY market bulunamadı. "
                "Binance API: %s",
                SETTINGS.base_url,
            )

            # Burada botu tamamen öldürmek yerine
            # taramaya devam etmesi için bekle.
            time.sleep(60)
            return

        log.info(
            "Binance doğrulandı | TRY:%d | API:%s",
            try_count,
            SETTINGS.base_url,
        )

    except Exception as e:

        log.exception(
            "MARKET DOĞRULAMA HATASI: %s",
            e,
        )

        time.sleep(60)
        return

    # --------------------------------------------------------
    # TELEGRAM AKTİVASYON
    # --------------------------------------------------------

    if (
        SETTINGS.telegram_token
        and SETTINGS.telegram_chat
    ):

        try:

            CLIENT.telegram(
                "🐋 BALİNA RADARI V26 AKTİF\n\n"
                "📐 MA7 / MA30 / MA99\n"
                "📦 Daralma + Bollinger sıkışması\n"
                "🚀 Kapanmış mum kırılımı\n"
                "📊 Hacim + alıcı baskısı\n"
                "📈 RSI + MACD + ADX\n"
                "💠 VWAP teyidi\n"
                "🛡️ Fakeout filtresi\n"
                "🚨 Tuzak filtresi\n"
                "🔁 Streak / tekrar sinyal takibi\n"
                "⚡ Rate-limit koruması aktif"
            )

        except Exception:

            log.exception(
                "Telegram aktivasyon mesajı gönderilemedi."
            )

    # --------------------------------------------------------
    # TEMİZLİK
    # --------------------------------------------------------

    last_cleanup = 0.0

    # --------------------------------------------------------
    # SÜREKLİ TARAMA
    # --------------------------------------------------------

    while True:

        started = time.time()

        try:

            backoff = scan()

        except Exception:

            log.exception(
                "Tarama döngüsü hatası"
            )

            backoff = True

        # ----------------------------------------------------
        # GÜNLÜK DB TEMİZLİĞİ
        # ----------------------------------------------------

        if (
            started - last_cleanup
            > 86400
        ):

            try:

                removed = (
                    DBS.cleanup_old_signals()
                )

                if removed:

                    log.info(
                        "%d eski sinyal silindi.",
                        removed,
                    )

            except Exception:

                log.exception(
                    "DB temizliği başarısız"
                )

            last_cleanup = started

        # ----------------------------------------------------
        # BEKLEME
        # ----------------------------------------------------

        elapsed = (
            time.time()
            - started
        )

        if backoff:

            sleep_time = max(
                180,
                SETTINGS.scan_interval * 3,
            )

        else:

            sleep_time = max(
                1,
                SETTINGS.scan_interval
                - elapsed,
            )

        log.info(
            "Sonraki tarama %.0f sn sonra.",
            sleep_time,
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():

    return (
        "🐋 Balina Radarı V26 Aktif"
    )


@app.route("/health")
def health():

    return {
        "status": "ok",
        "bot": "Balina Radarı V26",
        "scan_interval":
            SETTINGS.scan_interval,
        "workers":
            SETTINGS.workers,
        "rate_limit":
            LIMITER.snapshot(),
    }


@app.route("/performance")
def performance():

    try:

        samples = len(
            DBS.performance_summary()
        )

    except Exception:

        log.exception(
            "Performance sorgusu başarısız"
        )

        samples = 0

    return {
        "status": "ok",
        "bot": "Balina Radarı V26",
        "samples": samples,
    }


# ============================================================
# BACKGROUND THREAD
# ============================================================

Thread(
    target=loop,
    daemon=True,
    name="balina-v26",
).start()


# ============================================================
# FLASK SERVER
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
                )
