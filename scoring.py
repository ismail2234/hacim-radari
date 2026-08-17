from __future__ import annotations

import logging
from dataclasses import dataclass, field

from binance_client import BinanceClient
from config import Settings
from db import DB
from indicators import (
    adx, avg, bb, ema, macd, pct, rsi, vwap
)
from market import MarketData

log = logging.getLogger("balina.scoring")


@dataclass
class Features:
    price: float = 0.0

    # Momentum
    momentum1: float = 0.0
    momentum5: float = 0.0
    rv: float = 50.0
    old_rsi: float = 50.0

    # MA
    ma7: float = 0.0
    ma30: float = 0.0
    ma99: float = 0.0
    ma7_old: float = 0.0
    ma7_cross: bool = False
    ma_structure: bool = False

    # Daralma
    consolidation: bool = False
    consolidation_range: float = 0.0
    bb_width: float = 0.0
    squeeze: bool = False

    # Kırılım
    resistance: float = 0.0
    dist: float = 0.0
    breakout: bool = False
    closed_breakout: bool = False

    # Mum
    close_position: float = 50.0
    upper_wick_pct: float = 0.0
    strong_close: bool = False

    # Hacim
    vr: float = 0.0
    vr5: float = 0.0
    impulse: float = 1.0

    # Alıcı
    bp: float = 50.0

    # İşlem
    trades1: int = 0
    trades5: int = 0

    # Teknik teyit
    macd_up: bool = False
    ad: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    adx_rising: bool = False

    # Diğer
    higher_low: bool = False
    price_above_vwap: bool = False
    vwap_value: float = 0.0

    # Fakeout
    fakeout: bool = False
    fakeout_reasons: list[str] = field(default_factory=list)

    # Tuzak
    trap: bool = False
    trap_reasons: list[str] = field(default_factory=list)

    # OI
    oi_available: bool = False
    oi_change: float | None = None


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_consolidation(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    cfg: Settings,
) -> tuple[bool, float, float]:
    """
    V26 daralma motoru.

    Amaç:
    Uzun süre yatay kalan ve volatilitesi düşen coinleri
    kırılım öncesinde yakalamak.
    """

    bars = cfg.consolidation_bars

    if len(closes) < bars:
        return False, 0.0, 0.0

    c = closes[-bars:]
    h = highs[-bars:]
    l = lows[-bars:]

    high_value = max(h)
    low_value = min(l)

    if low_value <= 0:
        return False, 0.0, 0.0

    price = c[-1]

    range_pct = (
        (high_value - low_value) / price * 100
    )

    lower, middle, upper = bb(c, 20, 2)

    if middle > 0:
        bb_width = (
            (upper - lower) / middle * 100
        )
    else:
        bb_width = 0.0

    # Fiyat uzun süre dar bantta kalıyor
    range_ok = range_pct <= cfg.consolidation_max_range

    # Bollinger bantları da sıkışmış
    bb_ok = bb_width <= cfg.consolidation_max_bb_width

    return range_ok and bb_ok, range_pct, bb_width


def calculate_features(
    cfg: Settings,
    c1: list,
    close5: list[float],
    volume5: list[float],
    trades5_sum: int,
) -> Features:

    if len(c1) < 100:
        raise ValueError("1m veri yetersiz")

    close = [safe_float(x[4]) for x in c1]
    high = [safe_float(x[2]) for x in c1]
    low = [safe_float(x[3]) for x in c1]
    volume = [safe_float(x[7]) for x in c1]
    trades = [int(safe_float(x[8])) for x in c1]

    price = close[-1]

    # ---------------------------------------------------------
    # MA7 / MA30 / MA99
    # ---------------------------------------------------------

    ma7 = ema(close, cfg.ma_fast)
    ma30 = ema(close, cfg.ma_mid)
    ma99 = ema(close, cfg.ma_slow)

    ma7_old = ema(
        close[:-2],
        cfg.ma_fast
    )

    ma7_cross = (
        ma7 > ma30
        and ma7_old <= ema(
            close[:-2],
            cfg.ma_mid
        )
    )

    ma_structure = (
        price > ma7
        and ma7 > ma30
        and ma30 > ma99
    )

    # ---------------------------------------------------------
    # Momentum
    # ---------------------------------------------------------

    momentum1 = pct(close[-2], price)

    momentum5 = (
        pct(close5[-4], price)
        if len(close5) >= 4 else 0.0
    )

    current_rsi = rsi(close)
    old_rsi = rsi(close[:-3])

    # ---------------------------------------------------------
    # MACD
    # ---------------------------------------------------------

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])

    macd_up = macd_now > macd_old

    # ---------------------------------------------------------
    # ADX
    # ---------------------------------------------------------

    ad, plus_di, minus_di = adx(
        high, low, close
    )

    if len(close) > 40:
        ad_prev, _, _ = adx(
            high[:-3],
            low[:-3],
            close[:-3]
        )
    else:
        ad_prev = ad

    adx_rising = ad > ad_prev

    # ---------------------------------------------------------
    # Hacim
    # ---------------------------------------------------------

    avg_volume = avg(volume[-30:])

    recent_volume = avg(volume[-3:])

    vr = (
        recent_volume / avg_volume
        if avg_volume else 0.0
    )

    avg5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])

    vr5 = (
        recent5 / avg5
        if avg5 else 0.0
    )

    previous_volume = avg(volume[-10:-3])

    impulse = (
        recent_volume / previous_volume
        if previous_volume else 1.0
    )

    impulse = min(impulse, 10.0)

    # ---------------------------------------------------------
    # Alıcı baskısı
    # Binance kline 10. alan = taker buy quote volume
    # ---------------------------------------------------------

    buy_volume = sum(
        safe_float(x[10])
        for x in c1[-5:]
    )

    total_volume = sum(
        safe_float(x[7])
        for x in c1[-5:]
    )

    bp = (
        buy_volume / total_volume * 100
        if total_volume else 50.0
    )

    trades1 = sum(trades[-5:])

    # ---------------------------------------------------------
    # 5m işlem sayısı
    # ---------------------------------------------------------

    trades5 = trades5_sum

    # ---------------------------------------------------------
    # Daralma
    # ---------------------------------------------------------

    consolidation, range_pct, bb_width = (
        calculate_consolidation(
            close,
            high,
            low,
            cfg,
        )
    )

    squeeze = consolidation

    # ---------------------------------------------------------
    # Direnç / kırılım
    # Son tamamlanmış direnç bölgesi
    # ---------------------------------------------------------

    lookback = cfg.breakout_lookback

    resistance_slice = high[
        -(lookback + 2):-2
    ]

    resistance = (
        max(resistance_slice)
        if resistance_slice else price
    )

    dist = (
        max(
            0.0,
            (resistance - price)
            / price * 100
        )
        if price > 0 else 0.0
    )

    breakout = (
        price >
        resistance * (
            1 + cfg.breakout_buffer / 100
        )
    )

    # ---------------------------------------------------------
    # KAPANMIŞ MUM
    #
    # c1'in son elemanı zaten son kapanmış 1m mum.
    # Intrabar veri burada kullanılmıyor.
    # ---------------------------------------------------------

    closed_breakout = breakout

    candle_high = high[-1]
    candle_low = low[-1]
    candle_close = close[-1]
    candle_open = safe_float(c1[-1][1])

    candle_range = candle_high - candle_low

    if candle_range > 0:
        close_position = (
            (candle_close - candle_low)
            / candle_range * 100
        )

        upper_wick = candle_high - max(
            candle_open,
            candle_close
        )

        upper_wick_pct = (
            upper_wick / candle_range * 100
        )
    else:
        close_position = 50.0
        upper_wick_pct = 0.0

    strong_close = (
        close_position
        >= cfg.fakeout_min_close_position
    )

    # ---------------------------------------------------------
    # Higher Low
    # ---------------------------------------------------------

    higher_low = (
        len(low) >= 6
        and low[-1] > low[-3]
        and low[-3] >= low[-6]
    )

    # ---------------------------------------------------------
    # VWAP
    # ---------------------------------------------------------

    vwap_value = vwap(
        high,
        low,
        close,
        volume,
    )

    price_above_vwap = (
        vwap_value > 0
        and price > vwap_value
    )

    return Features(
        price=price,

        momentum1=momentum1,
        momentum5=momentum5,
        rv=current_rsi,
        old_rsi=old_rsi,

        ma7=ma7,
        ma30=ma30,
        ma99=ma99,
        ma7_old=ma7_old,
        ma7_cross=ma7_cross,
        ma_structure=ma_structure,

        consolidation=consolidation,
        consolidation_range=range_pct,
        bb_width=bb_width,
        squeeze=squeeze,

        resistance=resistance,
        dist=dist,
        breakout=breakout,
        closed_breakout=closed_breakout,

        close_position=close_position,
        upper_wick_pct=upper_wick_pct,
        strong_close=strong_close,

        vr=vr,
        vr5=vr5,
        impulse=impulse,

        bp=bp,

        trades1=trades1,
        trades5=trades5,

        macd_up=macd_up,

        ad=ad,
        plus_di=plus_di,
        minus_di=minus_di,
        adx_rising=adx_rising,

        higher_low=higher_low,

        price_above_vwap=price_above_vwap,
        vwap_value=vwap_value,
    ) def apply_fakeout_filter(f: Features, cfg: Settings) -> Features:
    reasons = []

    if f.breakout and not f.strong_close:
        reasons.append("zayıf kapanış")

    if f.upper_wick_pct > cfg.fakeout_max_wick:
        reasons.append("uzun üst fitil")

    if f.breakout and f.bp < cfg.buyer_pressure_min:
        reasons.append("alıcı baskısı düşük")

    if f.breakout and f.vr < cfg.volume_ratio_buy:
        reasons.append("hacim teyidi zayıf")

    f.fakeout_reasons = reasons
    f.fakeout = bool(reasons)

    return f


def apply_trap_filter(f: Features, cfg: Settings) -> Features:
    reasons = []

    if f.vr >= cfg.volume_ratio_strong and f.bp < 50:
        reasons.append("yüksek hacim/zayıf alıcı")

    if f.momentum5 < -1.2 and not f.higher_low:
        reasons.append("negatif momentum")

    if f.breakout and f.vr < cfg.volume_ratio_buy:
        reasons.append("kırılım hacimsiz")

    if f.breakout and f.close_position < 50:
        reasons.append("kapanış zayıf")

    f.trap_reasons = reasons
    f.trap = bool(reasons)

    return f


def confirmation_count(f: Features, cfg: Settings) -> tuple[int, list[str]]:
    """
    V26'nın ana teyit motoru.

    AL için tek bir gösterge yeterli değildir.
    Birden fazla bağımsız teyit aynı anda aranır.
    """

    checks = []

    if f.consolidation:
        checks.append("Daralma")

    if f.ma7_cross:
        checks.append("MA7 kırılımı")

    if f.ma_structure:
        checks.append("MA7>MA30>MA99")

    if f.closed_breakout:
        checks.append("Kapanmış mum kırılımı")

    if f.vr >= cfg.volume_ratio_buy:
        checks.append(f"Hacim {f.vr:.1f}x")

    if f.bp >= cfg.buyer_pressure_min:
        checks.append(f"Alıcı %{f.bp:.0f}")

    if (
        cfg.rsi_min <= f.rv <= cfg.rsi_max
        and f.rv > f.old_rsi
    ):
        checks.append("RSI yükseliyor")

    if f.macd_up:
        checks.append("MACD güçleniyor")

    if f.ad >= cfg.adx_min and f.plus_di > f.minus_di:
        checks.append("ADX/+DI")

    if f.adx_rising:
        checks.append("ADX yükseliyor")

    if f.price_above_vwap:
        checks.append("VWAP üstü")

    if f.strong_close:
        checks.append("Güçlü kapanış")

    return len(checks), checks


def calculate_score(
    f: Features,
    cfg: Settings,
    confirmations: int,
) -> int:

    score = 0

    # Daralma
    if f.consolidation:
        score += 15

    # MA7 ana kırılımı
    if f.ma7_cross:
        score += 15

    if f.ma_structure:
        score += 10

    # Kapanış kırılımı
    if f.closed_breakout:
        score += 15

    # Hacim
    if f.vr >= cfg.volume_ratio_strong:
        score += 12
    elif f.vr >= cfg.volume_ratio_buy:
        score += 8

    # Alıcı baskısı
    if f.bp >= cfg.buyer_pressure_strong:
        score += 10
    elif f.bp >= cfg.buyer_pressure_min:
        score += 6

    # RSI
    if cfg.rsi_min <= f.rv <= cfg.rsi_max:
        if f.rv > f.old_rsi:
            score += 8

    # MACD
    if f.macd_up:
        score += 7

    # ADX
    if f.ad >= cfg.adx_strong and f.plus_di > f.minus_di:
        score += 10
    elif f.ad >= cfg.adx_min and f.plus_di > f.minus_di:
        score += 6

    # ADX ivmesi
    if f.adx_rising:
        score += 4

    # VWAP
    if f.price_above_vwap:
        score += 3

    # Güçlü mum kapanışı
    if f.strong_close:
        score += 5

    # Higher Low
    if f.higher_low:
        score += 3

    # Negatif durumlar
    if f.fakeout:
        score -= 20

    if f.trap:
        score -= 25

    if f.rv >= 80:
        score -= 10

    if f.momentum1 > 5:
        score -= 8

    return max(0, min(100, score))


def decide_stage(
    f: Features,
    cfg: Settings,
    score: int,
    confirmations: int,
) -> str:

    if f.trap or f.fakeout:
        return "PASS"

    # Çok önemli:
    # AL için kapanmış mum + hacim + momentum teyidi gerekiyor.
    momentum_ok = (
        f.macd_up
        or f.adx_rising
        or f.momentum1 >= cfg.momentum_min
    )

    volume_ok = f.vr >= cfg.volume_ratio_buy
    buyer_ok = f.bp >= cfg.buyer_pressure_min
    rsi_ok = cfg.rsi_min <= f.rv <= cfg.rsi_max

    # GÜÇLÜ AL
    if (
        score >= cfg.min_score_strong
        and f.closed_breakout
        and volume_ok
        and buyer_ok
        and momentum_ok
        and rsi_ok
        and f.ad >= cfg.adx_min
    ):
        return "VERY"

    # AL
    if (
        score >= cfg.min_score_buy
        and f.closed_breakout
        and volume_ok
        and momentum_ok
        and rsi_ok
        and confirmations >= 5
    ):
        return "BUY"

    # ÖNCÜ AL
    # Henüz tam kırılım oluşmamış olabilir.
    if (
        f.consolidation
        and f.ma7 > f.ma30
        and f.ma7 > f.ma7_old
        and f.vr >= 1.20
        and f.bp >= 55
        and f.rv < cfg.rsi_max
        and confirmations >= 4
    ):
        return "ONCU"

    return "PASS"


def trade_confidence(
    cfg: Settings,
    trades: int,
    volume_ratio: float,
) -> float:

    if trades <= 0:
        return 0.0

    if trades < 5:
        return 0.30

    if trades < 10:
        return 0.50

    if volume_ratio >= 2:
        return 1.0

    return min(1.0, trades / 50)


def analyze(
    cfg: Settings,
    client: BinanceClient,
    db: DB,
    market: MarketData,
    item: dict,
) -> dict:

    symbol = item["symbol"]

    try:
        # -----------------------------------------------------
        # 5M VERİ
        # -----------------------------------------------------

        k5 = client.klines(symbol, "5m", 80)

        if len(k5) < 40:
            return {"status": "PASS", "symbol": symbol}

        c5 = k5[:-1]

        close5 = [
            safe_float(x[4])
            for x in c5
        ]

        volume5 = [
            safe_float(x[7])
            for x in c5
        ]

        trades5_sum = sum(
            int(safe_float(x[8]))
            for x in c5[-1:]
        )

        # Çok sert düşen coinleri erken ele
        if len(close5) >= 5:
            early_momentum = pct(
                close5[-5],
                close5[-1]
            )

            if early_momentum < -3:
                return {
                    "status": "PASS",
                    "symbol": symbol,
                }

        # -----------------------------------------------------
        # 1M VERİ
        # -----------------------------------------------------

        k1 = client.klines(symbol, "1m", 180)

        if len(k1) < 110:
            return {"status": "PASS", "symbol": symbol}

        # Son açık mum çıkarılıyor.
        c1 = k1[:-1]

        f = calculate_features(
            cfg,
            c1,
            close5,
            volume5,
            trades5_sum,
        )

        # -----------------------------------------------------
        # FAKEOUT / TRAP
        # -----------------------------------------------------

        f = apply_fakeout_filter(f, cfg)
        f = apply_trap_filter(f, cfg)

        # -----------------------------------------------------
        # TEYİT
        # -----------------------------------------------------

        confirmations, criteria = confirmation_count(
            f,
            cfg,
        )

        score = calculate_score(
            f,
            cfg,
            confirmations,
        )

        stage = decide_stage(
            f,
            cfg,
            score,
            confirmations,
        )

        # -----------------------------------------------------
        # MARKET
        # -----------------------------------------------------

        market_ctx = market.context()

        market_momentum = market_ctx.get(
            "momentum",
            0.0,
        )

        # -----------------------------------------------------
        # GÜNLÜK TREND
        # -----------------------------------------------------

        trend = market.daily_trend(symbol)

        d30 = (
            trend.get("d30")
            if trend.get("ok")
            else None
        )

        d90 = (
            trend.get("d90")
            if trend.get("ok")
            else None
        )

        if not trend.get("ok"):
            trend_state = "VERİ YOK"
        elif d30 > 10 and d90 > 0:
            trend_state = "POZİTİF TREND"
        elif d90 < -65 or d30 < -35:
            trend_state = "YÜKSEK DÜŞÜŞ RİSKİ"
        elif d90 < -50 or d30 < -20:
            trend_state = "DÜŞÜŞ RİSKİ"
        else:
            trend_state = "NÖTR"

        # -----------------------------------------------------
        # GİRİŞ KALİTESİ
        # -----------------------------------------------------

        entry = 100

        if not f.closed_breakout:
            entry -= 20

        if f.vr < cfg.volume_ratio_buy:
            entry -= 15

        if f.bp < cfg.buyer_pressure_min:
            entry -= 15

        if f.ad < cfg.adx_min:
            entry -= 10

        if f.rv > 75:
            entry -= 10

        if f.fakeout:
            entry -= 25

        if f.trap:
            entry -= 30

        entry = max(0, min(100, entry))

        # -----------------------------------------------------
        # STOP LOSS
        #
        # Ana referans MA7.
        # MA7'nin hemen altı otomatik stop için gönderilecek.
        # -----------------------------------------------------

        stop_loss = (
            f.ma7 * 0.997
            if f.ma7 > 0
            else f.price * 0.995
        )

        stop_distance = (
            (f.price - stop_loss)
            / f.price * 100
            if f.price > 0
            else 0
        )

        # -----------------------------------------------------
        # DAHA ÖNCEKİ SİNYAL
        # -----------------------------------------------------

        previous = db.get_last_signal(symbol)

        previous_text = "İlk sinyal"

        if previous:
            previous_ts = previous.get("ts", 0)
            elapsed = __import__("time").time() - previous_ts

            if elapsed < cfg.repeat_window:
                mins = int(elapsed // 60)

                if mins < 1:
                    previous_text = "Daha önce: az önce"
                else:
                    previous_text = (
                        f"Daha önce: {mins} dk önce"
                    )

        # -----------------------------------------------------
        # SONUÇ
        # -----------------------------------------------------

        qualified = stage in (
            "ONCU",
            "BUY",
            "VERY",
        )

        streak = db.update_streak(
            symbol,
            qualified,
            f.trap,
        )

        return {
            "status": stage,
            "symbol": symbol,

            "score": score,
            "priority": score,
            "entry_quality": entry,

            "price": f.price,

            "chg": item.get("chg", 0),

            "momentum1": f.momentum1,
            "momentum5": f.momentum5,

            "rv": f.rv,
            "ad": f.ad,

            "ma7": f.ma7,
            "ma30": f.ma30,
            "ma99": f.ma99,
            "ma7_cross": f.ma7_cross,
            "ma_structure": f.ma_structure,

            "consolidation": f.consolidation,
            "consolidation_range": f.consolidation_range,
            "bb_width": f.bb_width,

            "dist": f.dist,

            "breakout": f.breakout,
            "closed_breakout": f.closed_breakout,

            "close_position": f.close_position,
            "upper_wick_pct": f.upper_wick_pct,
            "strong_close": f.strong_close,

            "vr": f.vr,
            "vr5": f.vr5,
            "impulse": f.impulse,

            "bp": f.bp,

            "trades_1m": f.trades1,
            "trades_5m": f.trades5,

            "macd": f.macd_up,

            "plus_di": f.plus_di,
            "minus_di": f.minus_di,
            "adx_rising": f.adx_rising,

            "higher_low": f.higher_low,

            "vwap_value": f.vwap_value,
            "price_above_vwap": f.price_above_vwap,

            "fakeout": f.fakeout,
            "fakeout_reasons": f.fakeout_reasons,

            "trap": f.trap,
            "trap_reasons": f.trap_reasons,

            "criteria_count": confirmations,
            "criteria_list": criteria,

            "d30": d30,
            "d90": d90,
            "trend_state": trend_state,

            "market_momentum": market_momentum,
            "market_state": market_ctx.get(
                "state",
                "VERİ YOK",
            ),

            "trade_conf": trade_confidence(
                cfg,
                f.trades1,
                f.vr,
            ),

            "streak": streak,

            "stop_loss": stop_loss,
            "stop_distance": stop_distance,

            "previous_signal": previous_text,

            # OI şu anda Binance TR spotta yok.
            "oi_available": False,
            "oi_change": None,
        }

    except Exception as e:

        log.warning(
            "%s V26 analiz hatası: %s",
            symbol,
            e,
            exc_info=True,
        )

        return {
            "status": "error",
            "symbol": symbol,
        }


def rank_signals(
    cfg: Settings,
    signals: list[dict],
) -> list[dict]:

    for r in signals:

        priority = float(
            r.get("score", 0)
        )

        if r.get("closed_breakout"):
            priority += 5

        if r.get("vr", 0) >= 2:
            priority += 4

        if r.get("bp", 0) >= 65:
            priority += 4

        if r.get("adx_rising"):
            priority += 3

        if r.get("price_above_vwap"):
            priority += 2

        if r.get("fakeout"):
            priority -= 20

        if r.get("trap"):
            priority -= 25

        r["priority"] = max(
            0,
            min(100, priority),
        )

    signals.sort(
        key=lambda x: (
            x.get("priority", 0),
            x.get("score", 0),
            x.get("entry_quality", 0),
        ),
        reverse=True,
    )

    for i, r in enumerate(signals, 1):
        r["rank"] = i

    return signals
