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


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def long_term_penalty(
    cfg: Settings,
    d30: float,
    d90: float,
) -> int:
    penalty = 0

    if d30 <= cfg.lt30_strong:
        penalty -= 8
    elif d30 <= cfg.lt30_mild:
        penalty -= 4

    if d90 <= cfg.lt90_extreme:
        penalty -= 15
    elif d90 <= cfg.lt90_strong:
        penalty -= 10
    elif d90 <= cfg.lt90_mild:
        penalty -= 5

    return penalty


def trade_confidence(
    cfg: Settings,
    trades: int,
    volume_ratio: float,
) -> float:
    if trades <= 0:
        return 0.0

    if (
        volume_ratio >= 2
        and trades < cfg.min_1m_trades
    ):
        return 0.25

    if trades < cfg.min_1m_trades:
        return 0.40

    return min(
        1.0,
        max(
            0.40,
            trades / cfg.trade_reference,
        ),
    )


def extract_features(
    cfg: Settings,
    c1: list,
    close5: list[float],
    volume5: list[float],
    price_5m_close: float,
    trades5_sum: int,
) -> Features:
    if len(c1) < 60:
        raise ValueError("1m veri yetersiz")

    close = [
        _safe_float(x[4])
        for x in c1
    ]

    high = [
        _safe_float(x[2])
        for x in c1
    ]

    low = [
        _safe_float(x[3])
        for x in c1
    ]

    volume = [
        _safe_float(x[7])
        for x in c1
    ]

    trades = [
        _safe_int(x[8])
        for x in c1
    ]

    if not close or close[-1] <= 0:
        raise ValueError("Geçersiz fiyat")

    price = close[-1]

    avg5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])

    vr5 = (
        recent5 / avg5
        if avg5 > 0
        else 0.0
    )

    momentum5 = pct(
        close5[-4],
        price,
    )

    momentum1 = pct(
        close[-2],
        price,
    )

    low_window = low[-90:]
    high_window = high[-90:]

    low90 = min(low_window)
    high90 = max(high_window)

    if high90 > low90:
        location = (
            (price - low90)
            / (high90 - low90)
            * 100
        )
    else:
        location = 50.0

    avg_volume = avg(volume[-30:])
    last3 = avg(volume[-3:])
    previous = avg(volume[-10:-3])

    vr = (
        last3 / avg_volume
        if avg_volume > 0
        else 0.0
    )

    impulse = min(
        last3 / previous
        if previous > 0
        else 1.0,
        10.0,
    )

    buy_volume = sum(
        _safe_float(x[10])
        for x in c1[-5:]
    )

    total_volume = sum(
        _safe_float(x[7])
        for x in c1[-5:]
    )

    bp = (
        buy_volume / total_volume * 100
        if total_volume > 0
        else 50.0
    )

    trades1 = sum(trades[-5:])

    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)

    ema9_old = ema(
        close[:-3],
        9,
    )

    ema21_old = ema(
        close[:-3],
        21,
    )

    ema_up = (
        ema9 > ema21
        and ema9 > ema9_old
    )

    ema_cross = (
        ema9 > ema21
        and ema9_old <= ema21_old
    )

    rv = rsi(close)
    old_rsi = rsi(close[:-3])

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])

    macd_up = macd_now > macd_old

    ad, plus_di, minus_di = adx(
        high,
        low,
        close,
    )

    lower, middle, upper = bb(close)

    width = (
        (upper - lower)
        / middle
        * 100
        if middle > 0
        else 0.0
    )

    old_lower, old_middle, old_upper = bb(
        close[:-5]
    )

    old_width = (
        (old_upper - old_lower)
        / old_middle
        * 100
        if old_middle > 0
        else width
    )

    squeeze = (
        width <= 2.2
        or (
            old_width > 0
            and width < old_width * 0.80
        )
    )

    expanding = (
        old_width > 0
        and width > old_width * 1.08
    )

    resistance_window = high[-30:-2]

    if not resistance_window:
        resistance_window = high[-20:]

    resistance = max(resistance_window)

    dist = max(
        0.0,
        (resistance - price)
        / price
        * 100,
    )

    breakout = price > resistance

    closed_breakout = close[-1] > resistance

    higher_low = (
        low[-1] > low[-3]
        and low[-3] >= low[-6]
    )

    candle_range = high[-1] - low[-1]

    close_position = (
        (close[-1] - low[-1])
        / candle_range
        * 100
        if candle_range > 0
        else 50.0
    )

    low_activity = (
        trades1 < cfg.min_1m_trades
        or trades5_sum < cfg.min_5m_trades
    )

    weak_volume = (
        vr < 1.0
        or vr5 < 1.0
    )

    trap_reasons: list[str] = []

    if (
        bp < cfg.trap_buyer
        and vr >= cfg.trap_volume
    ):
        trap_reasons.append("zayıf alıcı")

    if (
        momentum5 < cfg.trap_momentum
        and not higher_low
    ):
        trap_reasons.append("negatif momentum")

    if (
        low_activity
        and vr >= 2
    ):
        trap_reasons.append("düşük işlem")

    if (
        low_activity
        and weak_volume
        and bp >= 90
    ):
        trap_reasons.append(
            "güvenilmez baskı"
        )

    return Features(
        price=price,
        momentum1=momentum1,
        momentum5=momentum5,
        location=location,
        close_position=close_position,
        vr=vr,
        vr5=vr5,
        impulse=impulse,
        bp=bp,
        trades1=trades1,
        trades5=trades5_sum,
        ema_up=ema_up,
        ema_cross=ema_cross,
        price_above_ema50=price >= ema50,
        rv=rv,
        old_rsi=old_rsi,
        macd_up=macd_up,
        ad=ad,
        plus_di=plus_di,
        minus_di=minus_di,
        squeeze=squeeze,
        expanding=expanding,
        dist=dist,
        breakout=breakout,
        closed_breakout=closed_breakout,
        higher_low=higher_low,
        low_activity=low_activity,
        weak_volume=weak_volume,
        trap=bool(trap_reasons),
        trap_reasons=trap_reasons,
    )


def score_setup(
    cfg: Settings,
    f: Features,
) -> int:
    score = 0

    if f.ema_up:
        score += 12

    if f.ema_cross:
        score += 6

    if f.squeeze:
        score += 8

    if f.higher_low:
        score += 6

    if (
        35 <= f.rv <= 65
        and f.rv > f.old_rsi
    ):
        score += 8

    if f.price_above_ema50:
        score += 5

    if f.dist <= 0.70:
        score += 8

    if (
        f.vr >= 1.5
        and f.trades1 >= cfg.min_1m_trades
    ):
        score += 8

    if (
        f.bp >= 58
        and f.trades1 >= cfg.min_1m_trades
    ):
        score += 5

    return score


def score_confirmation(
    cfg: Settings,
    f: Features,
) -> int:
    score = 0

    if f.closed_breakout:
        score += 18
    elif f.breakout:
        score += 10

    if f.vr >= 2:
        score += 12
    elif f.vr >= 1.5:
        score += 7

    if f.vr5 >= 1.5:
        score += 8

    if (
        f.bp >= 65
        and f.trades1 >= cfg.min_1m_trades
    ):
        score += 7

    if f.macd_up:
        score += 6

    if (
        f.plus_di > f.minus_di
        and f.ad >= 18
    ):
        score += 7

    if f.close_position >= 65:
        score += 4

    if f.expanding:
        score += 4

    if (
        f.trades1 >= cfg.min_1m_trades
        and f.trades5 >= cfg.min_5m_trades
    ):
        score += 3

    if f.weak_volume:
        score -= 8

    if f.ad < 10:
        score -= 10

    if f.low_activity:
        score -= 8

    return score


def score_penalty(
    cfg: Settings,
    f: Features,
    d30: float | None,
    d90: float | None,
    market_momentum: float,
) -> int:
    penalty = (
        long_term_penalty(
            cfg,
            d30,
            d90,
        )
        if d30 is not None
        and d90 is not None
        else -5
    )

    if f.momentum1 > 2.5:
        penalty -= 10

    if f.momentum5 > 5:
        penalty -= 12

    if f.rv > 78:
        penalty -= 10

    if f.rv >= 85:
        penalty -= 8

    if (
        f.bp < 50
        and f.vr >= 1.8
    ):
        penalty -= 8

    if (
        f.momentum5 < -1.2
        and not f.higher_low
    ):
        penalty -= 12

    if (
        f.vr >= 2
        and f.trades1 < cfg.min_1m_trades
    ):
        penalty -= 8

    if f.trap:
        penalty -= 12

    if d90 is not None:
        if d90 <= cfg.lt90_extreme:
            penalty -= 8
        elif d90 <= cfg.lt90_strong:
            penalty -= 5

    if abs(market_momentum) >= cfg.market_move * 2:
        penalty -= 8

    elif abs(market_momentum) >= cfg.market_move:
        penalty -= 4

    return penalty


def score_entry_quality(
    cfg: Settings,
    f: Features,
    d30: float | None,
    d90: float | None,
) -> int:
    score = 100

    if f.rv >= 85:
        score -= 30
    elif f.rv >= 78:
        score -= 15

    if f.momentum1 >= 5:
        score -= 25
    elif f.momentum1 >= 2.5:
        score -= 12

    if f.momentum5 >= 5:
        score -= 20
    elif f.momentum5 >= 3:
        score -= 10

    if f.dist <= 0.15:
        score -= 8
    elif f.dist <= 0.35:
        score -= 4

    if f.closed_breakout:
        score += 5

    if f.higher_low:
        score += 5

    if f.trades1 < cfg.min_1m_trades:
        score -= 18

    if f.trades1 < 5:
        score -= 15

    if f.vr < 1.0:
        score -= 12

    if f.vr5 < 1.0:
        score -= 10

    if f.ad < 10:
        score -= 12

    if d30 is not None and d30 >= 20:
        score -= 5

    if (
        d90 is not None
        and d90 <= cfg.lt90_strong
    ):
        score -= 10

    if f.trap:
        score -= 20

    return clamp(score)


def decide_stage(
    cfg: Settings,
    f: Features,
    score: int,
