from __future__ import annotations

import logging
from dataclasses import dataclass, field

from binance_client import BinanceClient
from config import Settings
from db import DB
from indicators import adx, avg, bb, clamp, ema, macd, pct, rsi, soft_cap
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
    adx_rising: bool = False
    momentum_building: bool = False
    volume_accelerating: bool = False
    trades_accelerating: bool = False
    rsi_healthy_rising: bool = False
    trap_reasons: list[str] = field(default_factory=list)


def trade_confidence(cfg: Settings, trades: int, volume_ratio: float) -> float:
    if trades <= 0:
        return 0.0
    if volume_ratio >= 2 and trades < cfg.min_1m_trades:
        return 0.25
    if trades < cfg.min_1m_trades:
        return 0.40
    return min(1.0, max(0.40, trades / cfg.trade_reference))


def extract_features(cfg: Settings, c1: list, close5: list[float],
                     volume5: list[float], price_5m_close: float,
                     trades5_sum: int) -> Features:
    if len(c1) < 100 or len(close5) < 6 or len(volume5) < 12:
        raise ValueError("Yetersiz kline verisi")

    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]
    trades = [int(x[8]) for x in c1]

    price = close[-1]

    avg5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])
    vr5 = recent5 / avg5 if avg5 else 0.0
    momentum5 = pct(close5[-4], price)
    momentum1 = pct(close[-2], price)

    low90 = min(low[-90:])
    high90 = max(high[-90:])
    location = ((price - low90) / (high90 - low90) * 100
                if high90 > low90 else 50.0)

    avg_volume = avg(volume[-30:])
    last3 = avg(volume[-3:])
    previous = avg(volume[-10:-3])
    vr = last3 / avg_volume if avg_volume else 0.0
    impulse = min(last3 / previous if previous else 1.0, 10.0)

    buy_volume = sum(float(x[10]) for x in c1[-5:])
    total_volume = sum(float(x[7]) for x in c1[-5:])
    bp = buy_volume / total_volume * 100 if total_volume else 50.0
    trades1 = sum(trades[-5:])

    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)
    ema9_old = ema(close[:-3], 9)
    ema21_old = ema(close[:-3], 21)
    ema_up = ema9 > ema21 and ema9 > ema9_old
    ema_cross = ema9 > ema21 and ema9_old <= ema21_old

    rv = rsi(close)
    old_rsi = rsi(close[:-3])

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])
    macd_up = macd_now > macd_old

    ad, plus_di, minus_di = adx(high, low, close)

    lower, middle, upper = bb(close)
    width = (upper - lower) / middle * 100 if middle else 0.0
    old_lower, old_middle, old_upper = bb(close[:-5])
    old_width = ((old_upper - old_lower) / old_middle * 100
                 if old_middle else width)

    squeeze = width <= 2.2 or (old_width > 0 and width < old_width * 0.80)
    expanding = old_width > 0 and width > old_width * 1.08

    resistance = max(high[-30:-2])
    dist = max(0.0, (resistance - price) / price * 100)
    breakout = price > resistance
    closed_breakout = close[-1] > resistance

    higher_low = low[-1] > low[-3] and low[-3] >= low[-6]

    candle_range = high[-1] - low[-1]
    close_position = ((close[-1] - low[-1]) / candle_range * 100
                      if candle_range > 0 else 50.0)

    low_activity = (trades1 < cfg.min_1m_trades or
                    trades5_sum < cfg.min_5m_trades)
    weak_volume = vr < 1.0 or vr5 < 1.0

    if len(high) >= 14 * 2 + 1 + 3:
        ad_prev, _, _ = adx(high[:-3], low[:-3], close[:-3])
    else:
        ad_prev = ad
    adx_rising = ad > ad_prev

    m_prev = pct(close[-3], close[-2]) if len(close) >= 3 else 0.0
    momentum_building = momentum1 > 0 and momentum1 > m_prev

    if avg_volume and len(volume) >= 9:
        vr_a = avg(volume[-9:-6]) / avg_volume
        vr_b = avg(volume[-6:-3]) / avg_volume
        vr_c = avg(volume[-3:]) / avg_volume
        volume_accelerating = vr_c > vr_b >= vr_a
    else:
        volume_accelerating = False

    if len(trades) >= 15:
        trades_a = sum(trades[-15:-10])
        trades_b = sum(trades[-10:-5])
        trades_c = sum(trades[-5:])
        trades_accelerating = trades_c > trades_b >= trades_a
    else:
        trades_accelerating = False

    rsi_healthy_rising = (
        cfg.healthy_rsi_low <= rv <= cfg.healthy_rsi_high
        and rv > old_rsi
    )

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
        price=price, momentum1=momentum1, momentum5=momentum5,
        location=location, close_position=close_position, vr=vr, vr5=vr5,
        impulse=impulse, bp=bp, trades1=trades1, trades5=trades5_sum,
        ema_up=ema_up, ema_cross=ema_cross, price_above_ema50=price >= ema50,
        rv=rv, old_rsi=old_rsi, macd_up=macd_up, ad=ad,
        plus_di=plus_di, minus_di=minus_di, squeeze=squeeze,
        expanding=expanding, dist=dist, breakout=breakout,
        closed_breakout=closed_breakout, higher_low=higher_low,
        low_activity=low_activity, weak_volume=weak_volume,
        trap=bool(trap_reasons), adx_rising=adx_rising,
        momentum_building=momentum_building,
        volume_accelerating=volume_accelerating,
        trades_accelerating=trades_accelerating,
        rsi_healthy_rising=rsi_healthy_rising,
        trap_reasons=trap_reasons,
    )


def count_early_criteria(f: Features) -> tuple[int, list[str]]:
    criteria = []
    if f.adx_rising and f.plus_di > f.minus_di:
        criteria.append("ADX yükseliyor (+DI baskın)")
    if f.momentum_building:
        criteria.append("Momentum yeni oluşuyor")
    if f.volume_accelerating:
        criteria.append("Hacim ivmeleniyor")
    if f.trades_accelerating:
        criteria.append("İşlem sayısı ivmeleniyor")
    if f.rsi_healthy_rising:
        criteria.append("RSI sağlıklı bantta ve yükseliyor")
    if f.ema_up:
        criteria.append("EMA yukarı")
    if f.macd_up:
        criteria.append("MACD güçleniyor")
    return len(criteria), criteria


def score_setup(cfg: Settings, f: Features) -> int:
    setup = 0
    if f.ema_up: setup += 12
    if f.ema_cross: setup += 6
    if f.squeeze: setup += 8
    if f.higher_low: setup += 6
    if 35 <= f.rv <= 65 and f.rv > f.old_rsi: setup += 8
    if f.price_above_ema50: setup += 5
    if f.dist <= 0.35: setup += 6
    elif f.dist <= 0.70: setup += 3
    if f.vr >= 1.5 and f.trades1 >= cfg.min_1m_trades: setup += 8
    if f.bp >= 58 and f.trades1 >= cfg.min_1m_trades: setup += 5
    return setup


def score_confirmation(cfg: Settings, f: Features) -> int:
    confirmation = 0
    if f.closed_breakout: confirmation += 12
    elif f.breakout: confirmation += 5
    if f.vr >= 2: confirmation += 12
    elif f.vr >= 1.5: confirmation += 7
    if f.vr5 >= 1.5: confirmation += 8
    if f.bp >= 65 and f.trades1 >= cfg.min_1m_trades: confirmation += 7
    if f.macd_up: confirmation += 6
    if f.plus_di > f.minus_di and f.ad >= cfg.min_adx_trend:
        confirmation += 7
    elif f.plus_di > f.minus_di and f.ad >= 10:
        confirmation -= cfg.weak_adx_penalty
    if f.close_position >= 65: confirmation += 4
    if f.expanding: confirmation += 4
    if f.trades1 >= cfg.min_1m_trades and f.trades5 >= cfg.min_5m_trades:
        confirmation += 3
    if f.weak_volume: confirmation -= 8
    if f.ad < 10: confirmation -= 10
    if f.low_activity: confirmation -= 8
    return confirmation


def score_penalty(cfg: Settings, f: Features, d30: float | None,
                  d90: float | None, market_momentum: float) -> int:
    penalty = 0
    if f.momentum1 > 2.5: penalty -= 10
    if f.momentum5 > 5: penalty -= 12
    if f.rv > 78: penalty -= 10
    if f.rv >= 85: penalty -= 8
    if f.bp < 50 and f.vr >= 1.8: penalty -= 8
    if f.momentum5 < -1.2 and not f.higher_low: penalty -= 12
    if f.vr >= 2 and f.trades1 < cfg.min_1m_trades: penalty -= 8
    if f.trap: penalty -= 12
    if abs(market_momentum) >= cfg.market_move * 2: penalty -= 8
    elif abs(market_momentum) >= cfg.market_move: penalty -= 4
    return penalty


def score_entry_quality(cfg: Settings, f: Features,
                        d30: float | None, d90: float | None) -> int:
    entry = 100
    if f.rv >= 85: entry -= 30
    elif f.rv >= 78: entry -= 15
    if f.momentum1 >= 5: entry -= 25
    elif f.momentum1 >= 2.5: entry -= 12
    if f.momentum5 >= 5: entry -= 20
    elif f.momentum5 >= 3: entry -= 10
    if f.dist <= 0.15: entry -= 8
    elif f.dist <= 0.35: entry -= 4
    if f.closed_breakout: entry += 2
    if f.higher_low: entry += 3
    if f.trades1 < cfg.min_1m_trades: entry -= 18
    if f.trades1 < 5: entry -= 15
    if f.vr < 1.0: entry -= 12
    if f.vr5 < 1.0: entry -= 10
    if f.ad < 10: entry -= 12
    elif f.ad < cfg.min_adx_trend: entry -= cfg.weak_adx_penalty
    if d30 is not None and d30 >= 20: entry -= 5
    if f.trap: entry -= 20
    entry = max(0, min(100, entry))
    entry = soft_cap(entry, cfg.entry_soft_cap, cfg.entry_soft_cap_factor)
    return max(0, min(100, int(round(entry))))


def decide_stage(cfg: Settings, f: Features, criteria_count: int) -> str:
    if f.trap:
        return "PASS"

    stage = "PASS"

    if criteria_count >= cfg.oncu_min_criteria and f.rv < cfg.oncu_rsi_max:
        stage = "ONCU"

    momentum_confirmed = (
        f.momentum1 > cfg.al_momentum_confirm
        or f.vr >= cfg.al_volume_confirm
    )
    if criteria_count >= cfg.buy_min_criteria and momentum_confirmed:
        stage = "BUY"

    strong_trend_confirmed = (
        f.closed_breakout
        or (f.plus_di > f.minus_di and f.ad >= cfg.min_adx_trend)
    )
    if criteria_count >= cfg.very_min_criteria and strong_trend_confirmed:
        stage = "VERY"

    return stage


def trend_state_label(trend_ok: bool, d30: float | None, d90: float | None,
                      cfg: Settings) -> str:
    if not trend_ok or d30 is None or d90 is None:
        return "VERİ YOK"
    if d30 > 10 and d90 > 0: return "POZİTİF TREND"
    if d90 <= cfg.lt90_extreme or d30 <= cfg.lt30_strong:
        return "YÜKSEK DÜŞÜŞ RİSKİ"
    if d90 <= cfg.lt90_strong or d30 <= cfg.lt30_mild:
        return "DÜŞÜŞ RİSKİ"
    return "NÖTR"


def analyze(cfg: Settings, client: BinanceClient, db: DB,
            market: MarketData, item: dict) -> dict:
    symbol = item["symbol"]
    try:
        k5 = client.klines(symbol, "5m", 80)
        if len(k5) < 40:
            return {"status": "PASS", "symbol": symbol}

        c5 = k5[:-1]
        close5 = [float(x[4]) for x in c5]
        volume5 = [float(x[7]) for x in c5]
        trades5_list = [int(x[8]) for x in c5]

        price_estimate = close5[-1]
        avg5 = avg(volume5[-12:])
        recent5 = avg(volume5[-3:])
        vr5_early = recent5 / avg5 if avg5 else 0.0
        momentum5_early = pct(close5[-4], price_estimate)

        if momentum5_early < -3 and vr5_early < 1.3:
            return {"status": "PASS", "symbol": symbol}

        trend = market.daily_trend(symbol)
        d30 = trend.get("d30") if trend.get("ok") else None
        d90 = trend.get("d90") if trend.get("ok") else None

        k1 = client.klines(symbol, "1m", 180)
        if len(k1) < 100:
            return {"status": "PASS", "symbol": symbol}

        c1 = k1[:-1]

        # NOT: Mevcut davranışı koruyoruz; 5m listesinden son tamamlanmış
        # 5m mumun işlem sayısı kullanılıyor.
        trades5_sum = sum(trades5_list[-1:])

        f = extract_features(cfg, c1, close5, volume5,
                             price_estimate, trades5_sum)

        setup = score_setup(cfg, f)
        confirmation = score_confirmation(cfg, f)
        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0.0)

        penalty = score_penalty(cfg, f, d30, d90, market_momentum)

        raw_score = clamp(setup + confirmation + penalty)
        score = clamp(int(round(soft_cap(
            raw_score, cfg.score_soft_cap, cfg.score_soft_cap_factor
        ))))

        if f.low_activity: score = min(score, 78)
        if f.weak_volume: score = min(score, 82)
        if f.ad < 10: score = min(score, 72)
        elif f.ad < cfg.min_adx_trend: score = min(score, 80)
        if f.rv >= 90 and f.trades1 < cfg.min_1m_trades:
            score = min(score, 65)

        entry = score_entry_quality(cfg, f, d30, d90)
        criteria_count, criteria_list = count_early_criteria(f)
        stage = decide_stage(cfg, f, criteria_count)

        # KRİTİK DÜZELTME:
        # Eski bozuk satır "stage == levelqualified = ..." idi.
        # Burada level açıkça oluşturuluyor.
        level = {
            "ONCU": "ONCU",
            "BUY": "BUY",
            "VERY": "VERY",
        }.get(stage, "PASS")

        # Streak ilk uygun sinyali engellemez.
        qualified = stage in ("ONCU", "BUY", "VERY") and not f.trap
        streak = db.update_streak(symbol, qualified, f.trap)

        return {
            "status": level,
            "stage": stage,
            "symbol": symbol,
            "score": score,
            "setup": setup,
            "confirmation": confirmation,
            "penalty": penalty,
            "price": f.price,
            "chg": item.get("chg", 0.0),
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
            "trend_state": trend_state_label(trend.get("ok", False),
                                             d30, d90, cfg),
            "trap": f.trap,
            "trap_reasons": f.trap_reasons,
            "entry_quality": entry,
            "streak": streak,
            "market_momentum": market_momentum,
            "market_state": market_ctx.get("state", "VERİ YOK"),
            "criteria_count": criteria_count,
            "criteria_list": criteria_list,
            "adx_rising": f.adx_rising,
            "momentum_building": f.momentum_building,
            "volume_accelerating": f.volume_accelerating,
            "trades_accelerating": f.trades_accelerating,
            "rsi_healthy_rising": f.rsi_healthy_rising,
        }

    except Exception:
        log.exception("%s: analyze() hatası", symbol)
        return {"status": "error", "symbol": symbol}


def priority_score(cfg: Settings, r: dict) -> float:
    value = (
        r["score"] * 0.50
        + r["entry_quality"] * 0.25
        + r["trade_conf"] * 100 * 0.10
    )

    if r["streak"] >= 3: value += 8
    elif r["streak"] >= 2: value += 4

    if r["closed_breakout"]: value += 4
    elif r["breakout"]: value += 1

    if r["bp"] >= 75: value += 5
    elif r["bp"] >= 65: value += 3

    if r["vr"] >= 3: value += 5
    elif r["vr"] >= 2: value += 3
    elif r["vr"] >= 1.5: value += 1

    if r["vr5"] >= 2: value += 4
    elif r["vr5"] >= 1.5: value += 2

    if r["trades_1m"] < 5: value -= 15
    elif r["trades_1m"] < cfg.min_1m_trades: value -= 8

    if r["vr5"] < 0.75: value -= 8
    if r["ad"] < 10: value -= 8
    if r["rv"] >= 85: value -= 8
    if r["trap"]: value -= 25

    value = max(0, min(100, value))
    value = soft_cap(value, cfg.priority_soft_cap,
                     cfg.priority_soft_cap_factor)
    return max(0, min(100, round(value, 1)))


def rank_signals(cfg: Settings, signals: list[dict]) -> list[dict]:
    for r in signals:
        r["priority"] = priority_score(cfg, r)

    signals.sort(
        key=lambda x: (x["priority"], x["entry_quality"], x["score"]),
        reverse=True,
    )

    for i, r in enumerate(signals, 1):
        r["rank"] = i

    return signals
    
