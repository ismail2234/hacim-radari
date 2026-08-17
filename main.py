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

        symbol = ticker.get(
            "symbol",
            "",
        )

        # Sadece TRY marketleri.
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

            # Çok düşük hacimli,
            # aşırı yükselmiş veya geçersiz fiyat.
            if (
                volume
                < cfg.min_quote_volume
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

    def ranking(
        item: dict,
    ):

        volume = item.get(
            "volume",
            0,
        )

        change = max(
            item.get(
                "chg",
                0,
            ),
            0,
        )

        return (
            volume
            * (
                1
                + change / 100
            )
        )

    return sorted(
        items,
        key=ranking,
        reverse=True,
    )[
        :cfg.shortlist_size
    ]


# ============================================================
# TELEGRAM MESAJI
# ============================================================

def message(
    r: dict,
) -> str:

    titles = {
        "ONCU": "🟡 ÖNCÜ AL",
        "BUY": "🟢 AL",
        "VERY": "🚀 GÜÇLÜ AL",
    }

    title = titles.get(
        r.get(
            "status",
            "PASS",
        ),
        r.get(
            "status",
            "PASS",
        ),
    )

    criteria = r.get(
        "criteria_list",
        [],
    )

    criteria_text = (
        "\n".join(
            f"• {item}"
            for item in criteria
        )
        if criteria
        else "• Teyit yok"
    )

    d30_value = r.get(
        "d30"
    )

    d90_value = r.get(
        "d90"
    )

    d30 = (
        f"{d30_value:+.1f}%"
        if d30_value is not None
        else "VERİ YOK"
    )

    d90 = (
        f"{d90_value:+.1f}%"
        if d90_value is not None
        else "VERİ YOK"
    )

    fakeout = ""

    if r.get("fakeout"):

        reasons = r.get(
            "fakeout_reasons",
            [],
        )

        fakeout = (
            "\n⚠️ FAKEOUT: "
            + (
                ", ".join(reasons)
                if reasons
                else "Teyit zayıf"
            )
            + "\n"
        )

    trap = ""

    if r.get("trap"):

        reasons = r.get(
            "trap_reasons",
            [],
        )

        trap = (
            "\n🚨 TUZAK: "
            + (
                ", ".join(reasons)
                if reasons
                else "Riskli yapı"
            )
            + "\n"
        )

    if r.get(
        "oi_available",
        False,
    ):

        oi = (
            f"{r.get('oi_change', 0):+.2f}%"
        )

    else:

        oi = "Yok"

    return (
        "🐋 BALİNA RADARI V26\n\n"

        f"{title}\n"
        "━━━━━━━━━━━━━━\n"

        f"🪙 #{r.get('symbol', '?')}\n"
        f"💰 Fiyat: "
        f"{r.get('price', 0):.8g}\n\n"

        f"💪 Skor: "
        f"{r.get('score', 0):.0f}/100\n"

        f"🏆 Öncelik: "
        f"{r.get('priority', 0):.0f}/100\n"

        f"🎯 Giriş kalitesi: "
        f"{r.get('entry_quality', 0):.0f}/100\n\n"

        "📐 MA YAPISI\n"

        f"MA7: "
        f"{r.get('ma7', 0):.8g}\n"

        f"MA30: "
        f"{r.get('ma30', 0):.8g}\n"

        f"MA99: "
        f"{r.get('ma99', 0):.8g}\n"

        f"MA7 kırılımı: "
        f"{'✅' if r.get('ma7_cross') else '⏳'}\n\n"

        "📦 DARALMA\n"

        f"Daralma: "
        f"{'✅' if r.get('consolidation') else '❌'}\n"

        f"Aralık: "
        f"%{r.get('consolidation_range', 0):.2f}\n"

        f"BB genişliği: "
        f"%{r.get('bb_width', 0):.2f}\n\n"

        "🚀 KIRILIM\n"

        f"Kırılım: "
        f"{'✅' if r.get('breakout') else '⏳'}\n"

        f"Kapanmış mum: "
        f"{'✅' if r.get('closed_breakout') else '⏳'}\n"

        f"Direnç mesafesi: "
        f"%{r.get('dist', 0):.2f}\n\n"

        "📊 HACİM / ALICI\n"

        f"1m hacim: "
        f"{r.get('vr', 0):.2f}x\n"

        f"5m hacim: "
        f"{r.get('vr5', 0):.2f}x\n"

        f"İvme: "
        f"{r.get('impulse', 0):.2f}x\n"

        f"Alıcı baskısı: "
        f"%{r.get('bp', 0):.0f}\n"

        f"1m işlem: "
        f"{r.get('trades_1m', 0)}\n\n"

        "📈 MOMENTUM\n"

        f"RSI: "
        f"{r.get('rv', 0):.1f}\n"

        f"ADX: "
        f"{r.get('ad', 0):.1f}\n"

        f"ADX yükseliyor: "
        f"{'✅' if r.get('adx_rising') else '❌'}\n"

        f"MACD: "
        f"{'✅' if r.get('macd') else '❌'}\n\n"

        "🎯 TEYİTLER\n"

        f"{criteria_text}\n\n"

        f"💠 VWAP: "
        f"{'ÜZERİNDE ✅' if r.get('price_above_vwap') else 'ALTINDA'}\n"

        f"🛑 Stop referansı: "
        f"{r.get('stop_loss', 0):.8g}\n"

        f"📉 Stop mesafesi: "
        f"%{r.get('stop_distance', 0):.2f}\n\n"

        f"📅 30g: {d30} | 90g: {d90}\n"

        f"🌐 BTC/TRY: "
        f"{r.get('market_momentum', 0):+.2f}%\n\n"

        f"🔁 Teyit: "
        f"{r.get('streak', 0)}x\n"

        f"🕘 "
        f"{r.get('previous_signal', 'İlk sinyal')}\n"

        f"🟣 Open Interest: {oi}\n"

        f"{fakeout}"
        f"{trap}\n"

        "⚠️ Yatırım tavsiyesi değildir."
    )# ============================================================
# TEK COIN ANALİZİ
# ============================================================

def analyze_one(
    item: dict,
) -> dict:

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

        # ----------------------------------------------------
        # 24 SAATLİK TICKER
        # ----------------------------------------------------

        all_candidates = CLIENT.tickers()

        if not all_candidates:

            log.warning(
                "Ticker verisi alınamadı."
            )

            return True

        # ----------------------------------------------------
        # TRY ADAYLARI
        # ----------------------------------------------------

        items = candidates(
            SETTINGS,
            all_candidates,
        )

        if not items:

            log.info(
                "Uygun TRY coin bulunamadı."
            )

            return False

        # ----------------------------------------------------
        # SHORTLIST
        # ----------------------------------------------------

        items = shortlist(
            SETTINGS,
            items,
        )

        log.info(
            "Tarama başladı | "
            "TRY:%d | Shortlist:%d",
            len(
                [
                    x
                    for x in all_candidates
                    if str(
                        x.get(
                            "symbol",
                            "",
                        )
                    ).endswith("TRY")
                ]
            ),
            len(items),
        )

        # ----------------------------------------------------
        # PARALEL ANALİZ
        # ----------------------------------------------------

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
            if r.get(
                "status"
            ) in (
                "ONCU",
                "BUY",
                "VERY",
            )
        ]

        # ----------------------------------------------------
        # SIRALAMA
        # ----------------------------------------------------

        try:

            ranked = rank_signals(
                signals,
                SETTINGS,
            )

        except TypeError:

            # Eski/uyumsuz imza ihtimaline karşı
            ranked = rank_signals(
                signals
            )

        except Exception:

            log.exception(
                "rank_signals hatası"
            )

            ranked = signals

        # ----------------------------------------------------
        # EN FAZLA MAX_SIGNALS
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

            # PASS gönderme.
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

            text = message(
                result
            )

            reply_to = None

            try:

                old = DBS.get_last_signal(
                    symbol
                )

                if old:

                    # Son Telegram mesaj ID'si
                    # DB'de tutulmuyorsa reply
                    # kullanılmaz.
                    reply_to = old.get(
                        "telegram_message_id"
                    )

            except Exception:

                reply_to = None

            message_id = CLIENT.telegram(
                text,
                reply_to=reply_to,
            )

            if message_id:

                sent += 1

                try:

                    DBS.create_signal(
                        result
                    )

                except Exception:

                    log.exception(
                        "Sinyal DB kaydı başarısız | %s",
                        symbol,
                    )

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
                x.get(
                    "symbol"
                ): float(
                    x.get(
                        "lastPrice",
                        0,
                    )
                )
                for x in all_candidates
                if x.get(
                    "symbol"
                )
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

        # Çok fazla hata veya tarama
        # beklenenden çok uzun sürüyorsa
        # bir sonraki turda backoff.
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
            > SETTINGS.scan_interval
            * 1.25
        )

    except Exception:

        log.exception(
            "SCAN genel hatası"
        )

        return True# ============================================================
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

        symbols = {
            x.get("symbol")
            for x in info.get(
                "symbols",
                [],
            )
        }

        try_count = sum(
            1
            for symbol in symbols
            if symbol
            and symbol.endswith("TRY")
        )

        if try_count <= 0:
            raise RuntimeError(
                "TRY market bulunamadı."
            )

        log.info(
            "Binance doğrulandı | TRY:%d",
            try_count,
        )

    except Exception as e:

        log.exception(
            "MARKET DOĞRULAMA HATASI: %s",
            e,
        )

        return

    # --------------------------------------------------------
    # TELEGRAM AKTİVASYON MESAJI
    # --------------------------------------------------------

    if (
        SETTINGS.telegram_token
        and SETTINGS.telegram_chat
    ):

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

    # --------------------------------------------------------
    # TEMİZLİK ZAMANI
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

            # Hata veya API yoğunluğu varsa
            # sistemi biraz dinlendir.
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
