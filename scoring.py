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
    volume_building: bool = False
    pressure_building: bool = False
    momentum_building: bool = False


def long_term_penalty(cfg: Settings, d30: float | None, d90: float | None) -> int:
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


def trade_confidence(cfg: Settings, trades: int, volume_ratio: float) -> float:
    if trades <= 0:
        return 0.0
    if volume_ratio >= 2 and trades < cfg.min_1m_trades:
        return 0.25
    if trades < cfg.min_1m_trades:
        return 0.40
    return min(1.0, max(0.40, trades / cfg.trade_reference))


def extract_features(
    cfg: Settings,
    c1: list,
    close5: list[float],
    volume5: list[float],
    price_5m_close: float,
    trades5_sum: int,
) -> Features:
    if len(c1) < 100 or len(close5) < 10:
        raise ValueError("veri yetersiz")

    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]
    trades = [int(x[8]) for x in c1]

    price = close[-1]

    base5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])
    vr5 = recent5 / base5 if base5 else 0.0
    momentum5 = pct(close5[-4], price)
    momentum1 = pct(close[-2], price)

    lo = min(low[-90:])
    hi = max(high[-90:])
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

    rv = rsi(close)
    old_rsi = rsi(close[:-3])

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])
    macd_up = macd_now > macd_old

    ad, plus_di, minus_di = adx(high, low, close)

    lower, middle, upper = bb(close)
    width = (upper - lower) / middle * 100 if middle else 0.0
    old_lower, old_middle, old_upper = bb(close[:-5])
    old_width = (
        (old_upper - old_lower) / old_middle * 100
        if old_middle else width
    )

    squeeze = width <= 2.2 or (old_width > 0 and width < old_width * 0.80)
    expanding = old_width > 0 and width > old_width * 1.08

    resistance = max(high[-30:-2])
    dist = max(0.0, (resistance - price) / price * 100)
    breakout = price > resistance
    closed_breakout = close[-1] > resistance

    higher_low = low[-1] > low[-3] and low[-3] >= low[-6]
    candle_range = high[-1] - low[-1]
    close_position = (
        (close[-1] - low[-1]) / candle_range * 100
        if candle_range > 0 else 50.0
    )

    low_activity = (
        trades1 < cfg.min_1m_trades
        or trades5_sum < cfg.min_5m_trades
    )
    weak_volume = vr < 1.0 or vr5 < 1.0

    base_volume = avg(volume[-20:-3])
    volume_building = base_volume > 0 and last3 >= base_volume * 1.20
    pressure_building = bp >= 55
    momentum_building = (
        momentum1 > 0
        and momentum5 > 0
        and (macd_up or ema_up or higher_low)
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
    if rv >= 90 and momentum5 >= 5:
        reasons.append("aşırı uzama")
    if breakout and bp < 45 and vr5 >= 1.5:
        reasons.append("kırılımda zayıf alıcı")

    return Features(
        price, momentum1, momentum5, location, close_position,
        vr, vr5, impulse, bp, trades1, trades5_sum,
        ema_up, ema_cross, price >= ema50, rv, old_rsi, macd_up,
        ad, plus_di, minus_di, squeeze, expanding, dist, breakout,
        closed_breakout, higher_low, low_activity, weak_volume,
        bool(reasons), reasons, volume_building, pressure_building,
        momentum_building,
    )


def signal_groups(cfg: Settings, f: Features) -> tuple[int, dict[str, bool]]:
    groups = {
        "volume": (
            f.vr >= 1.35 or f.vr5 >= 1.35 or f.volume_building
        ),
        "participation": (
            f.trades1 >= cfg.min_1m_trades
            and f.trades5 >= cfg.min_5m_trades
        ),
        "buyers": f.bp >= 55 and f.pressure_building,
        "trend": (
            f.ema_up or f.ema_cross
            or (f.plus_di > f.minus_di and f.ad >= 15)
        ),
        "momentum": (
            f.momentum_building
            or f.macd_up
            or (f.momentum1 > 0 and f.momentum5 > 0)
        ),
        "structure": (
            f.higher_low
            or f.dist <= 1.50
            or f.squeeze
            or f.breakout
        ),
    }
    return sum(groups.values()), groups


def score_setup(cfg: Settings, f: Features) -> int:
    s = 0
    if f.ema_up:
        s += 8
    if f.ema_cross:
        s += 5
    if f.squeeze:
        s += 6
    if f.higher_low:
        s += 7
    if 35 <= f.rv <= 70 and f.rv > f.old_rsi:
        s += 6
    if f.price_above_ema50:
        s += 4
    if f.dist <= 1.50:
        s += 7
    if f.vr >= 1.35:
        s += 7
    if f.vr5 >= 1.35:
        s += 6
    if f.bp >= 55:
        s += 6
    if f.trades1 >= cfg.min_1m_trades:
        s += 5
    if f.volume_building:
        s += 5
    if f.momentum_building:
        s += 5
    return s


def score_confirmation(cfg: Settings, f: Features) -> int:
    s = 0
    if f.closed_breakout:
        s += 14
    elif f.breakout:
        s += 8
    if f.vr >= 2:
        s += 10
    elif f.vr >= 1.5:
        s += 6
    if f.vr5 >= 1.5:
        s += 7
    if f.bp >= 65 and f.trades1 >= cfg.min_1m_trades:
        s += 7
    elif f.bp >= 55:
        s += 3
    if f.macd_up:
        s += 6
    if f.plus_di > f.minus_di and f.ad >= 18:
        s += 6
    if f.close_position >= 65:
        s += 4
    if f.expanding:
        s += 4
    if f.trades1 >= cfg.min_1m_trades:
        s += 3
    if f.weak_volume:
        s -= 7
    if f.ad < 10:
        s -= 9
    if f.low_activity:
        s -= 7
    if f.breakout and f.bp < 50:
        s -= 8
    return s


def score_penalty(
    cfg: Settings,
    f: Features,
    d30: float | None,
    d90: float | None,
    market_momentum: float,
) -> int:
    p = long_term_penalty(cfg, d30, d90) if (d30 is not None or d90 is not None) else -5

    if f.momentum1 > 2.5:
        p -= 7
    if f.momentum5 > 5:
        p -= 10
    if f.rv >= 70:
        p -= 3
    if f.rv >= 78:
        p -= 7
    if f.rv >= 85:
        p -= 9
    if f.bp < 50 and f.vr >= 1.8:
        p -= 8
    if f.momentum5 < -1.2 and not f.higher_low:
        p -= 12
    if f.vr >= 2 and f.trades1 < cfg.min_1m_trades:
        p -= 8
    if f.trap:
        p -= 12

    if d90 is not None:
        if d90 <= cfg.lt90_extreme:
            p -= 6
        elif d90 <= cfg.lt90_strong:
            p -= 4

    if abs(market_momentum) >= cfg.market_move * 2:
        p -= 8
    elif abs(market_momentum) >= cfg.market_move:
        p -= 4

    return p


def score_entry_quality(
    cfg: Settings,
    f: Features,
    d30: float | None,
    d90: float | None,
) -> int:
    q = 100

    # RSI is a risk modifier, not an automatic rejection.
    if f.rv >= 70:
        q -= 4
    if f.rv >= 78:
        q -= 8
    if f.rv >= 85:
        q -= 15
    if f.rv >= 92:
        q -= 20

    if f.momentum1 >= 5:
        q -= 22
    elif f.momentum1 >= 2.5:
        q -= 10

    if f.momentum5 >= 5:
        q -= 18
    elif f.momentum5 >= 3:
        q -= 8

    if f.dist <= 0.15:
        q -= 7
    elif f.dist <= 0.35:
        q -= 3

    if f.closed_breakout:
        q += 5
    if f.higher_low:
        q += 5
    if f.volume_building:
        q += 4
    if f.momentum_building:
        q += 4

    if f.trades1 < cfg.min_1m_trades:
        q -= 15
    if f.trades1 < 5:
        q -= 15
    if f.vr < 1.0:
        q -= 10
    if f.vr5 < 1.0:
        q -= 8
    if f.ad < 10:
        q -= 10

    if d30 is not None and d30 >= 20:
        q -= 5
    if d90 is not None and d90 <= cfg.lt90_strong:
        q -= 8

    if f.trap:
        q -= 20

    return max(0, min(100, int(round(q))))


def decide_stage(
    cfg: Settings,
    f: Features,
    score: int,
    setup: int,
    confirmation: int,
    d30: float | None,
    d90: float | None,
) -> tuple[str, int]:
    count, groups = signal_groups(cfg, f)

    momentum_ok = (
        groups["momentum"]
        and (f.momentum1 > 0 or f.momentum5 > 0 or f.macd_up)
    )

    # 3-4 independent groups are enough for an early signal.
    if count >= 3 and not f.trap and score >= 60:
        if count <= 4:
            return "PRE_BREAKOUT", count

    # 4-5 groups plus momentum -> normal BUY.
    if count >= 4 and momentum_ok and score >= 66:
        return "CONFIRMED", count

    very_trend_ok = (
        d30 is not None and d90 is not None
        and d30 > cfg.lt30_strong
        and d90 > cfg.lt90_strong
    )

    # 6+ groups are strong, but breakout is preferred rather than mandatory.
    if (
        count >= 6
        and score >= 82
        and confirmation >= 20
        and not f.trap
        and not f.low_activity
        and f.ad >= 15
        and f.bp >= 55
        and (f.closed_breakout or very_trend_ok)
    ):
        return "STRONG", count

    if setup >= 25:
        return "SETUP", count

    return "NONE", count


def trend_state_label(
    trend_ok: bool,
    d30: float | None,
    d90: float | None,
    cfg: Settings,
) -> str:
    if not trend_ok or d30 is None or d90 is None:
        return "VERİ YOK"
    if d30 > 10 and d90 > 0:
        return "POZİTİF TREND"
    if d90 <= cfg.lt90_extreme or d30 <= cfg.lt30_strong:
        return "YÜKSEK DÜŞÜŞ RİSKİ"
    if d90 <= cfg.lt90_strong or d30 <= cfg.lt30_mild:
        return "DÜŞÜŞ RİSKİ"
    return "NÖTR"


def analyze(
    cfg: Settings,
    client: BinanceClient,
    db: DB,
    market: MarketData,
    item: dict,
) -> dict:
    symbol = item["symbol"]

    try:
        k5 = client.klines(symbol, "5m", 80)
        if len(k5) < 40:
            return {"status": "PASS", "symbol": symbol}

        c5 = k5[:-1]
        close5 = [float(x[4]) for x in c5]
        volume5 = [float(x[7]) for x in c5]
        trades5_list = [int(x[8]) for x in c5]

        early_avg = avg(volume5[-12:])
        early_recent = avg(volume5[-3:])
        early_vr = early_recent / early_avg if early_avg else 0
        early_momentum = pct(close5[-4], close5[-1])

        if early_momentum < -3 and early_vr < 1.3:
            return {"status": "PASS", "symbol": symbol}

        trend = market.daily_trend(symbol)
        d30 = trend["d30"] if trend["ok"] else None
        d90 = trend["d90"] if trend["ok"] else None

        k1 = client.klines(symbol, "1m", 180)
        if len(k1) < 100:
            return {"status": "PASS", "symbol": symbol}

        c1 = k1[:-1]
        trades5_sum = sum(trades5_list[-3:])

        f = extract_features(
            cfg, c1, close5, volume5, close5[-1], trades5_sum
        )

        setup = score_setup(cfg, f)
        confirmation = score_confirmation(cfg, f)
        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0.0)

        penalty = score_penalty(
            cfg, f, d30, d90, market_momentum
        )
        score = clamp(setup + confirmation + penalty)

        # Safety gates: quality can cap a score, but does not require
        # a breakout or a fixed streak count.
        if f.low_activity:
            score = min(score, 78)
        if f.weak_volume:
            score = min(score, 82)
        if f.ad < 10:
            score = min(score, 72)
        if f.breakout and f.bp < 50:
            score = min(score, 84)
        if f.rv >= 92 and f.trades1 < cfg.min_1m_trades:
            score = min(score, 65)

        entry = score_entry_quality(cfg, f, d30, d90)
        stage, group_count = decide_stage(
            cfg, f, score, setup, confirmation, d30, d90
        )

        level = {
            "STRONG": "VERY",
            "CONFIRMED": "BUY",
            "PRE_BREAKOUT": "BUY",
            "SETUP": "INTERNAL",
        }.get(stage, "PASS")

        qualified = (
            stage in ("PRE_BREAKOUT", "CONFIRMED", "STRONG", "SETUP")
            and not f.trap
        )

        streak = db.update_streak(symbol, qualified, f.trap)

        # Streak is informational/quality context, never a gate for the
        # first early signal.
        if level == "VERY" and streak < cfg.very_streak:
            level = "BUY"

        return {
            "status": level,
            "symbol": symbol,
            "phase": stage,
            "group_count": group_count,
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
            "trend_state": trend_state_label(
                trend["ok"], d30, d90, cfg
            ),
            "trap": f.trap,
            "trap_reasons": f.trap_reasons,
            "entry_quality": entry,
            "streak": streak,
            "market_momentum": market_momentum,
            "market_state": market_ctx.get("state", "VERİ YOK"),
            "volume_building": f.volume_building,
            "pressure_building": f.pressure_building,
            "momentum_building": f.momentum_building,
        }

    except Exception as e:
        log.exception("%s analyze hatası: %s", symbol, e)
        return {"status": "error", "symbol": symbol}


def priority_score(cfg: Settings, r: dict) -> float:
    value = (
        r["score"] * 0.50
        + r["entry_quality"] * 0.25
        + r["trade_conf"] * 100 * 0.10
    )

    if r["streak"] >= 3:
        value += 5
    elif r["streak"] >= 2:
        value += 2

    if r["phase"] == "PRE_BREAKOUT":
        value += 3
    elif r["phase"] == "STRONG":
        value += 5

    if r["closed_breakout"]:
        value += 7
    elif r["breakout"]:
        value += 3

    if r["bp"] >= 75:
        value += 5
    elif r["bp"] >= 65:
        value += 3
    elif r["bp"] < 45:
        value -= 8

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
