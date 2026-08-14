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
    pre_breakout: bool = False
    volume_building: bool = False
    pressure_building: bool = False
    volatility_expanding: bool = False
    early_score: int = 0
    extension: float = 0.0


def long_term_penalty(cfg, d30, d90):
    p = 0
    if d30 is not None:
        if d30 <= cfg.lt30_strong:
            p -= 8
        elif d30 <= cfg.lt30_mild:
            p -= 4
    if d90 is not None:
        if d90 <= cfg.lt90_extreme:
            p -= 15
        elif d90 <= cfg.lt90_strong:
            p -= 10
        elif d90 <= cfg.lt90_mild:
            p -= 5
    return p


def trade_confidence(cfg, trades, volume_ratio):
    if trades <= 0:
        return 0.0
    if volume_ratio >= 2 and trades < cfg.min_1m_trades:
        return 0.25
    if trades < cfg.min_1m_trades:
        return 0.40
    return min(1.0, max(0.40, trades / cfg.trade_reference))


def extract_features(cfg, c1, close5, volume5, price_5m_close, trades5_sum):
    if len(c1) < 60 or len(close5) < 6:
        raise ValueError("veri yetersiz")

    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]
    trades = [int(x[8]) for x in c1]
    price = close[-1]

    avg5 = avg(volume5[-12:])
    vr5 = avg(volume5[-3:]) / avg5 if avg5 else 0.0
    momentum5 = pct(close5[-4], price)
    momentum1 = pct(close[-2], price)

    lo, hi = min(low[-90:]), max(high[-90:])
    location = (price - lo) / (hi - lo) * 100 if hi > lo else 50.0

    avg_volume = avg(volume[-30:])
    last3 = avg(volume[-3:])
    previous = avg(volume[-10:-3])
    vr = last3 / avg_volume if avg_volume else 0.0
    impulse = min(last3 / previous if previous else 1.0, 10.0)

    buy_volume = sum(float(x[10]) for x in c1[-5:])
    total_volume = sum(float(x[7]) for x in c1[-5:])
    bp = buy_volume / total_volume * 100 if total_volume else 50.0
    trades1 = sum(trades[-5:])

    ema9, ema21, ema50 = ema(close, 9), ema(close, 21), ema(close, 50)
    ema9_old, ema21_old = ema(close[:-3], 9), ema(close[:-3], 21)
    ema_up = ema9 > ema21 and ema9 > ema9_old
    ema_cross = ema9 > ema21 and ema9_old <= ema21_old

    rv, old_rsi = rsi(close), rsi(close[:-3])
    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])
    macd_up = macd_now > macd_old
    ad, plus_di, minus_di = adx(high, low, close)

    lower, middle, upper = bb(close)
    width = (upper - lower) / middle * 100 if middle else 0.0
    old_lower, old_middle, old_upper = bb(close[:-5])
    old_width = (old_upper - old_lower) / old_middle * 100 if old_middle else width
    squeeze = width <= 2.2 or (old_width > 0 and width < old_width * 0.80)
    expanding = old_width > 0 and width > old_width * 1.08

    resistance = max(high[-30:-2])
    dist = max(0.0, (resistance - price) / price * 100)
    breakout = price > resistance
    closed_breakout = close[-1] > resistance
    higher_low = low[-1] > low[-3] and low[-3] >= low[-6]

    candle_range = high[-1] - low[-1]
    close_position = ((close[-1] - low[-1]) / candle_range * 100) if candle_range > 0 else 50.0

    low_activity = trades1 < cfg.min_1m_trades or trades5_sum < cfg.min_5m_trades
    weak_volume = vr < 1.0 or vr5 < 1.0

    recent_volume = avg(volume[-3:])
    base_volume = avg(volume[-20:-3])
    volume_building = base_volume > 0 and recent_volume >= base_volume * 1.15
    pressure_building = bp >= 53
    volatility_expanding = expanding or impulse >= 1.20

    early_score = 0
    early_score += 15 if higher_low else 0
    early_score += 15 if volume_building else 0
    early_score += 10 if vr5 >= 1.10 else 0
    early_score += 12 if pressure_building else 0
    early_score += 12 if ema_up else 0
    early_score += 10 if ema_cross else 0
    early_score += 10 if macd_up else 0
    early_score += 12 if 42 <= rv <= 68 and rv > old_rsi else 0
    early_score += 5 if close_position >= 55 else 0
    early_score += 7 if volatility_expanding else 0
    early_score += 10 if 0 < dist <= 1.80 else 0
    early_score += 8 if squeeze else 0
    if rv >= 78:
        early_score -= 10
    if momentum1 >= 4:
        early_score -= 8
    if momentum5 >= 6:
        early_score -= 10
    early_score = max(0, min(100, early_score))

    conditions = sum([
        higher_low,
        volume_building or vr5 >= 1.10,
        pressure_building,
        ema_up or ema_cross,
        macd_up,
        40 <= rv <= 68 and rv >= old_rsi,
        squeeze or expanding,
        close_position >= 55,
        volatility_expanding,
    ])

    pre_breakout = (
        not breakout
        and 0 < dist <= 1.80
        and conditions >= 4
        and early_score >= 48
        and momentum5 > -1.50
        and not (rv >= 82 and momentum5 >= 4)
    )

    reasons = []
    if bp < cfg.trap_buyer and vr >= cfg.trap_volume:
        reasons.append("zayıf alıcı")
    if momentum5 < cfg.trap_momentum and not higher_low:
        reasons.append("negatif momentum")
    if low_activity and vr >= 2:
        reasons.append("düşük işlem")
    if low_activity and weak_volume and bp >= 90:
        reasons.append("güvenilmez baskı")
    if rv >= 88 and momentum5 >= 3:
        reasons.append("aşırı uzama")
    if breakout and bp < 45 and vr5 >= 1.5:
        reasons.append("kırılımda zayıf alıcı")

    extension = max(0.0, momentum1 * 0.35 + momentum5 * 0.65 + max(0.0, rv - 70) * 0.08)

    return Features(
        price, momentum1, momentum5, location, close_position, vr, vr5,
        impulse, bp, trades1, trades5_sum, ema_up, ema_cross,
        price >= ema50, rv, old_rsi, macd_up, ad, plus_di, minus_di,
        squeeze, expanding, dist, breakout, closed_breakout, higher_low,
        low_activity, weak_volume, bool(reasons), reasons, pre_breakout,
        volume_building, pressure_building, volatility_expanding,
        early_score, extension
    )


def score_setup(cfg, f):
    s = 0
    if f.ema_up: s += 12
    if f.ema_cross: s += 7
    if f.squeeze: s += 7
    if f.higher_low: s += 8
    if 35 <= f.rv <= 68 and f.rv > f.old_rsi: s += 9
    if f.price_above_ema50: s += 5
    if f.dist <= 0.90: s += 7
    if f.vr >= 1.35 and f.trades1 >= max(3, int(cfg.min_1m_trades * 0.75)): s += 7
    if f.bp >= 56 and f.trades1 >= max(3, int(cfg.min_1m_trades * 0.75)): s += 5
    if f.pre_breakout: s += 10
    if f.volume_building: s += 5
    if f.pressure_building: s += 4
    if f.macd_up: s += 4
    if f.early_score >= 60: s += 6
    return s


def score_confirmation(cfg, f):
    s = 0
    if f.closed_breakout: s += 18
    elif f.breakout: s += 10
    if f.vr >= 2: s += 12
    elif f.vr >= 1.5: s += 7
    if f.vr5 >= 1.5: s += 8
    if f.bp >= 65 and f.trades1 >= cfg.min_1m_trades: s += 7
    if f.macd_up: s += 6
    if f.plus_di > f.minus_di and f.ad >= 18: s += 7
    if f.close_position >= 65: s += 4
    if f.expanding: s += 4
    if f.trades1 >= cfg.min_1m_trades and f.trades5 >= cfg.min_5m_trades: s += 3
    if f.weak_volume: s -= 6
    if f.ad < 10: s -= 8
    if f.low_activity: s -= 3
    if f.breakout and f.bp < 50: s -= 7
    if f.breakout and f.impulse < 0.80: s -= 5
    return s


def score_penalty(cfg, f, d30, d90, market_momentum):
    p = long_term_penalty(cfg, d30, d90) if d30 is not None or d90 is not None else -5
    if f.momentum1 > 2.5: p -= 10
    if f.momentum5 > 5: p -= 12
    if f.rv > 78: p -= 10
    if f.rv >= 85: p -= 8
    if f.bp < 50 and f.vr >= 1.8: p -= 8
    if f.momentum5 < -1.2 and not f.higher_low: p -= 12
    if f.vr >= 2 and f.trades1 < cfg.min_1m_trades: p -= 6
    if f.trap: p -= 12
    if d90 is not None:
        if d90 <= cfg.lt90_extreme: p -= 8
        elif d90 <= cfg.lt90_strong: p -= 5
    if abs(market_momentum) >= cfg.market_move * 2: p -= 8
    elif abs(market_momentum) >= cfg.market_move: p -= 4
    if f.pre_breakout and f.early_score >= 60 and not f.trap: p += 3
    return p


def score_entry_quality(cfg, f, d30, d90):
    q = 100
    if f.rv >= 85: q -= 30
    elif f.rv >= 78: q -= 15
    if f.momentum1 >= 5: q -= 25
    elif f.momentum1 >= 2.5: q -= 12
    if f.momentum5 >= 5: q -= 20
    elif f.momentum5 >= 3: q -= 10
    if f.dist <= 0.15: q -= 8
    elif f.dist <= 0.35: q -= 4
    if f.closed_breakout: q += 5
    if f.higher_low: q += 5
    if f.trades1 < cfg.min_1m_trades: q -= 8
    if f.trades1 < 5: q -= 10
    if f.vr < 1.0: q -= 10
    if f.vr5 < 1.0: q -= 8
    if f.ad < 10: q -= 10
    if d30 is not None and d30 >= 20: q -= 5
    if d90 is not None and d90 <= cfg.lt90_strong: q -= 10
    if f.trap: q -= 20
    if f.pre_breakout and f.rv < 72: q += 8
    if f.early_score >= 65 and not f.trap: q += 4
    if f.breakout and f.bp < 50: q -= 10
    if f.breakout and f.impulse < 0.80: q -= 8
    return max(0, min(100, int(round(q))))


def decide_stage(cfg, f, score, setup, confirmation, d30, d90):
    if f.pre_breakout and f.early_score >= 48 and setup >= 24 and score >= 58 and not f.trap and f.ad >= 10:
        return "PRE_BREAKOUT"

    stage = "SETUP" if setup >= 25 else "NONE"

    if score >= 66 and confirmation >= 16 and not f.weak_volume:
        stage = "CONFIRMED"

    very_ok = (
        d30 is not None and d90 is not None
        and d30 > cfg.lt30_strong and d90 > cfg.lt90_strong
        and not f.weak_volume and f.ad >= 18 and f.rv < 85
        and not f.trap and f.bp >= 55
    )

    if (
        score >= 84 and confirmation >= 28 and f.vr >= 1.5
        and f.vr5 >= 1.0 and very_ok and f.closed_breakout
        and f.bp >= 55 and f.impulse >= 0.80
    ):
        stage = "VERY"

    return stage


def trend_state_label(trend_ok, d30, d90, cfg):
    if not trend_ok or d30 is None or d90 is None:
        return "VERİ YOK"
    if d30 > 10 and d90 > 0:
        return "POZİTİF TREND"
    if d90 <= cfg.lt90_extreme or d30 <= cfg.lt30_strong:
        return "YÜKSEK DÜŞÜŞ RİSKİ"
    if d90 <= cfg.lt90_strong or d30 <= cfg.lt30_mild:
        return "DÜŞÜŞ RİSKİ"
    return "NÖTR"


def analyze(cfg, client, db, market, item):
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
        d30 = trend["d30"] if trend["ok"] else None
        d90 = trend["d90"] if trend["ok"] else None

        k1 = client.klines(symbol, "1m", 180)
        if len(k1) < 100:
            return {"status": "PASS", "symbol": symbol}

        c1 = k1[:-1]
        trades5_sum = sum(trades5_list[-3:])

        f = extract_features(cfg, c1, close5, volume5, price_estimate, trades5_sum)
        setup = score_setup(cfg, f)
        confirmation = score_confirmation(cfg, f)

        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0.0)
        penalty = score_penalty(cfg, f, d30, d90, market_momentum)
        score = clamp(setup + confirmation + penalty)

        if f.low_activity and not f.pre_breakout:
            score = min(score, 78)
        if f.weak_volume:
            score = min(score, 82)
        if f.ad < 10:
            score = min(score, 72)
        if f.rv >= 90 and f.trades1 < cfg.min_1m_trades:
            score = min(score, 65)
        if d90 is not None and d90 <= cfg.lt90_extreme:
            score = min(score, 82)
        if f.breakout and f.bp < 50:
            score = min(score, 84)
        if f.breakout and f.impulse < 0.80:
            score = min(score, 84)

        entry = score_entry_quality(cfg, f, d30, d90)
        stage = decide_stage(cfg, f, score, setup, confirmation, d30, d90)

        if stage == "VERY":
            level = "VERY"
        elif stage in ("CONFIRMED", "PRE_BREAKOUT"):
            level = "BUY"
        elif stage == "SETUP":
            level = "INTERNAL"
        else:
            level = "PASS"

        qualified = stage in ("SETUP", "CONFIRMED", "VERY", "PRE_BREAKOUT") and not f.trap
        streak = db.update_streak(symbol, qualified, f.trap)

        if level == "BUY" and stage != "PRE_BREAKOUT" and streak < cfg.buy_streak:
            level = "INTERNAL"
        if level == "VERY" and streak < cfg.very_streak:
            level = "INTERNAL"

        return {
            "status": level,
            "symbol": symbol,
            "phase": stage,
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
            "pre_breakout": f.pre_breakout,
            "volume_building": f.volume_building,
            "pressure_building": f.pressure_building,
            "volatility_expanding": f.volatility_expanding,
            "early_score": f.early_score,
            "extension": f.extension,
        }

    except Exception as e:
        log.exception("%s analyze hatası: %s", symbol, e)
        return {"status": "error", "symbol": symbol}


def priority_score(cfg, r):
    v = (
        r["score"] * 0.50
        + r["entry_quality"] * 0.25
        + r["trade_conf"] * 100 * 0.10
    )

    if r["streak"] >= 3:
        v += 8
    elif r["streak"] >= 2:
        v += 4

    if r["closed_breakout"]:
        v += 8
    elif r["breakout"]:
        v += 2
    elif r.get("pre_breakout"):
        v += 4

    early = r.get("early_score", 0)
    if early >= 75:
        v += 7
    elif early >= 65:
        v += 5
    elif early >= 55:
        v += 3

    if r["bp"] >= 75:
        v += 5
    elif r["bp"] >= 65:
        v += 3
    elif r["bp"] < 45:
        v -= 8

    if r["vr"] >= 3:
        v += 5
    elif r["vr"] >= 2:
        v += 3
    elif r["vr"] >= 1.5:
        v += 1

    if r["trades_1m"] < 5:
        v -= 15
    elif r["trades_1m"] < cfg.min_1m_trades:
        v -= 8

    if r["vr5"] < 0.75:
        v -= 8
    if r["ad"] < 10:
        v -= 8
    if r["rv"] >= 85:
        v -= 8
    if r["breakout"] and r["bp"] < 50:
        v -= 10
    if r["breakout"] and r["impulse"] < 0.80:
        v -= 8

    d90 = r["d90"] if r["d90"] is not None else 0.0
    d30 = r["d30"] if r["d30"] is not None else 0.0

    if d90 <= cfg.lt90_extreme:
        v -= 12
    elif d90 <= cfg.lt90_strong:
        v -= 8
    elif d90 <= cfg.lt90_mild:
        v -= 4

    if d30 <= cfg.lt30_strong:
        v -= 6
    elif d30 <= cfg.lt30_mild:
        v -= 3

    if r["trap"]:
        v -= 25

    return max(0, min(100, round(v, 1)))


def rank_signals(cfg, signals):
    for r in signals:
        r["priority"] = priority_score(cfg, r)

    signals.sort(
        key=lambda x: (
            x["priority"],
            x["entry_quality"],
            x["score"],
        ),
        reverse=True,
    )

    for i, r in enumerate(signals, 1):
        r["rank"] = i

    return signals
    
