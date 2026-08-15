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
    momentum_accel: float
    location: float
    close_position: float

    vr: float
    vr5: float
    impulse: float

    volume_rising: bool
    volume_accel_ratio: float

    bp: float
    trades1: int
    trades5: int
    trades_accel_ratio: float
    trades_rising: bool

    ema_up: bool
    ema_cross: bool
    price_above_ema50: bool

    rv: float
    old_rsi: float
    rsi_rising: bool

    macd_up: bool
    macd_hist: float
    macd_accel: bool

    ad: float
    ad_old: float
    plus_di: float
    minus_di: float
    adx_rising: bool
    di_bullish: bool

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


@dataclass
class Criteria:
    adx_direction: bool
    momentum_new: bool
    volume_acceleration: bool
    trade_acceleration: bool
    rsi_direction: bool
    ema_direction: bool
    macd_direction: bool

    def count(self) -> int:
        return sum([
            self.adx_direction,
            self.momentum_new,
            self.volume_acceleration,
            self.trade_acceleration,
            self.rsi_direction,
            self.ema_direction,
            self.macd_direction,
        ])

    def names(self) -> list[str]:
        names = []
        if self.adx_direction:
            names.append("ADX")
        if self.momentum_new:
            names.append("Momentum")
        if self.volume_acceleration:
            names.append("Hacim")
        if self.trade_acceleration:
            names.append("İşlem")
        if self.rsi_direction:
            names.append("RSI")
        if self.ema_direction:
            names.append("EMA")
        if self.macd_direction:
            names.append("MACD")
        return names


def long_term_risk(d30: float | None, d90: float | None, cfg: Settings) -> list[str]:
    reasons = []

    if d30 is not None:
        if d30 <= cfg.lt30_strong:
            reasons.append(f"30g {d30:+.1f}%")
        elif d30 <= cfg.lt30_mild:
            reasons.append(f"30g {d30:+.1f}%")

    if d90 is not None:
        if d90 <= cfg.lt90_extreme:
            reasons.append(f"90g {d90:+.1f}%")
        elif d90 <= cfg.lt90_strong:
            reasons.append(f"90g {d90:+.1f}%")
        elif d90 <= cfg.lt90_mild:
            reasons.append(f"90g {d90:+.1f}%")

    return reasons


def trade_confidence(cfg: Settings, trades: int, volume_ratio: float) -> float:
    if trades <= 0:
        return 0.0

    if trades < cfg.trade_reference:
        base = trades / cfg.trade_reference
    else:
        base = 1.0

    if volume_ratio >= 2 and trades < cfg.min_1m_trades:
        base *= 0.60

    return min(1.0, max(0.20, base))


def _avg_ratio(recent: list[float], previous: list[float]) -> float:
    a = avg(recent)
    b = avg(previous)
    return a / b if b > 0 else 0.0


def _rising_three(values: list[float], min_step: float = 0.0) -> bool:
    if len(values) < 3:
        return False
    return (
        values[-3] > 0
        and values[-2] >= values[-3] * (1 + min_step)
        and values[-1] >= values[-2] * (1 + min_step)
    )


def extract_features(
    cfg: Settings,
    c1: list,
    c5: list,
) -> Features:
    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]
    trades = [int(x[8]) for x in c1]

    close5 = [float(x[4]) for x in c5]
    volume5 = [float(x[7]) for x in c5]
    trades5_series = [int(x[8]) for x in c5]

    price = close[-1]

    # 5m hacim oranı: son 3 kapanmış 5m bar / önceki 9 bar.
    avg5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])
    vr5 = recent5 / avg5 if avg5 else 0.0

    momentum5 = pct(close5[-4], price)

    # Son 1 dakikadaki kısa momentum.
    momentum1 = pct(close[-2], price)

    # Momentumun yeni oluşup oluşmadığı.
    m1 = [
        pct(close[i - 1], close[i])
        for i in range(max(1, len(close) - 5), len(close))
    ]
    momentum_accel = (m1[-1] - avg(m1[:-1])) if len(m1) >= 2 else 0.0
    momentum_new = (
        len(m1) >= 3
        and m1[-1] > 0
        and m1[-2] > 0
        and m1[-3] > 0
    )

    low90 = min(low[-90:])
    high90 = max(high[-90:])
    location = (
        (price - low90) / (high90 - low90) * 100
        if high90 > low90 else 50
    )

    avg_volume = avg(volume[-30:])
    last3 = avg(volume[-3:])
    previous = avg(volume[-10:-3])
    vr = last3 / avg_volume if avg_volume else 0.0
    impulse = min(last3 / previous if previous else 1.0, 10.0)

    # Üç ardışık 1m hacim penceresi.
    vol_windows = [
        avg(volume[-15:-10]),
        avg(volume[-10:-5]),
        avg(volume[-5:]),
    ]
    volume_rising = _rising_three(vol_windows, 0.03)
    volume_accel_ratio = (
        vol_windows[-1] / vol_windows[-2]
        if vol_windows[-2] > 0 else 0.0
    )

    # Alıcı baskısı.
    buy_volume = sum(float(x[10]) for x in c1[-5:])
    total_volume = sum(float(x[7]) for x in c1[-5:])
    bp = buy_volume / total_volume * 100 if total_volume else 50.0

    trades1 = sum(trades[-5:])

    trade_recent = sum(trades[-5:])
    trade_previous = sum(trades[-10:-5])
    trades_accel_ratio = (
        trade_recent / trade_previous
        if trade_previous >= cfg.trade_accel_min_previous else 0.0
    )
    trades_rising = trades_accel_ratio >= cfg.trade_accel_ratio

    trades5 = sum(trades5_series[-1:]) if trades5_series else 0

    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    ema50 = ema(close, 50)
    ema9_old = ema(close[:-3], 9)
    ema21_old = ema(close[:-3], 21)

    ema_up = ema9 > ema21 and ema9 > ema9_old
    ema_cross = ema9 > ema21 and ema9_old <= ema21_old

    rv = rsi(close)
    old_rsi = rsi(close[:-3])
    rsi_rising = rv > old_rsi

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])
    macd_up = macd_now > macd_old

    # MACD histogram yönü.
    macd_main, macd_signal, macd_hist = macd(close)
    old_main, old_signal, old_hist = macd(close[:-3])
    macd_accel = macd_hist > old_hist

    ad, plus_di, minus_di = adx(high, low, close)
    ad_old, _, _ = adx(high[:-3], low[:-3], close[:-3])
    adx_rising = ad > ad_old
    di_bullish = plus_di > minus_di

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

    low_activity = (
        trades1 < cfg.min_1m_trades
        and trades_accel_ratio < cfg.trade_accel_strong_ratio
    )
    weak_volume = vr < 1.0 or vr5 < 1.0

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
        price=price,
        momentum1=momentum1,
        momentum5=momentum5,
        momentum_accel=momentum_accel,
        location=location,
        close_position=(
            (close[-1] - low[-1]) / (high[-1] - low[-1]) * 100
            if high[-1] > low[-1] else 50
        ),
        vr=vr,
        vr5=vr5,
        impulse=impulse,
        volume_rising=volume_rising,
        volume_accel_ratio=volume_accel_ratio,
        bp=bp,
        trades1=trades1,
        trades5=trades5,
        trades_accel_ratio=trades_accel_ratio,
        trades_rising=trades_rising,
        ema_up=ema_up,
        ema_cross=ema_cross,
        price_above_ema50=price >= ema50,
        rv=rv,
        old_rsi=old_rsi,
        rsi_rising=rsi_rising,
        macd_up=macd_up,
        macd_hist=macd_hist,
        macd_accel=macd_accel,
        ad=ad,
        ad_old=ad_old,
        plus_di=plus_di,
        minus_di=minus_di,
        adx_rising=adx_rising,
        di_bullish=di_bullish,
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


def build_criteria(cfg: Settings, f: Features) -> Criteria:
    adx_direction = f.adx_rising and f.di_bullish

    momentum_new = (
        f.momentum1 > 0
        and f.momentum_accel >= cfg.momentum_accel_min
    ) or (
        f.momentum1 > 0
        and f.momentum5 > 0
        and f.higher_low
    )

    volume_acceleration = (
        f.volume_rising
        and f.volume_accel_ratio >= cfg.volume_accel_min_ratio
    ) or (
        f.vr >= cfg.volume_accel_early_ratio
        and f.volume_accel_ratio >= cfg.volume_accel_strong_ratio
    )

    trade_acceleration = (
        f.trades_rising
        and f.trades_accel_ratio >= cfg.trade_accel_ratio
    )

    rsi_direction = (
        cfg.rsi_early_min <= f.rv <= cfg.rsi_early_max
        and f.rsi_rising
    )

    ema_direction = f.ema_up
    macd_direction = f.macd_up and f.macd_accel

    return Criteria(
        adx_direction=adx_direction,
        momentum_new=momentum_new,
        volume_acceleration=volume_acceleration,
        trade_acceleration=trade_acceleration,
        rsi_direction=rsi_direction,
        ema_direction=ema_direction,
        macd_direction=macd_direction,
    )


def score_setup(cfg: Settings, f: Features, c: Criteria) -> int:
    score = c.count() * 8

    if f.higher_low:
        score += 3
    if f.squeeze:
        score += 3
    if f.price_above_ema50:
        score += 3
    if f.bp >= 58:
        score += 3
    if f.dist <= 0.70:
        score += 3
    if f.dist <= 0.35:
        score += 2

    return score


def score_confirmation(cfg: Settings, f: Features, c: Criteria) -> int:
    score = 0

    if f.vr >= 2:
        score += 10
    elif f.vr >= 1.5:
        score += 6

    if f.vr5 >= 2:
        score += 8
    elif f.vr5 >= 1.5:
        score += 4

    if f.bp >= 65:
        score += 6
    elif f.bp >= 58:
        score += 3

    if f.macd_up:
        score += 5
        
    if f.adx_rising and f.di_bullish:
        score += 5

    if f.ad >= cfg.min_adx_trend and f.di_bullish:
        score += 5

    if f.close_position >= 65:
        score += 3

    if f.expanding:
        score += 3

    if f.trades_rising:
        score += 4

    if f.closed_breakout:
        score += 6
    elif f.breakout:
        score += 3

    if f.weak_volume:
        score -= 6

    if f.ad < 10:
        score -= 6

    return score


def score_penalty(
    cfg: Settings,
    f: Features,
    d30: float | None,
    d90: float | None,
    market_momentum: float,
) -> int:
    penalty = 0

    # Uzun vadeli risk artık tier/score gate değildir.
    # Sadece çok aşırı durumlarda küçük bir giriş kalitesi etkisi bırakılır.
    if d90 is not None and d90 <= cfg.lt90_extreme:
        penalty -= 3

    if f.momentum1 > 4:
        penalty -= 8
    elif f.momentum1 > 2.5:
        penalty -= 4

    if f.momentum5 > 8:
        penalty -= 6

    if f.rv >= cfg.rsi_extreme:
        penalty -= 10
    elif f.rv >= cfg.rsi_overheated:
        penalty -= 5

    if f.bp < 50 and f.vr >= 1.8:
        penalty -= 6

    if f.momentum5 < cfg.trap_momentum and not f.higher_low:
        penalty -= 8

    if f.trap:
        penalty -= 12

    if abs(market_momentum) >= cfg.market_move * 2:
        penalty -= 5
    elif abs(market_momentum) >= cfg.market_move:
        penalty -= 2

    return penalty


def score_entry_quality(
    cfg: Settings,
    f: Features,
    d30: float | None,
    d90: float | None,
) -> int:
    entry = 100

    if f.rv >= cfg.rsi_extreme:
        entry -= 30
    elif f.rv >= cfg.rsi_overheated:
        entry -= 15
    elif f.rv > cfg.rsi_early_max:
        entry -= 6

    if f.momentum1 >= 5:
        entry -= 22
    elif f.momentum1 >= 2.5:
        entry -= 10

    if f.momentum5 >= 5:
        entry -= 15
    elif f.momentum5 >= 3:
        entry -= 7

    if f.dist <= 0.15:
        entry += 5
    elif f.dist <= 0.35:
        entry += 3

    if f.higher_low:
        entry += 3

    if f.volume_rising:
        entry += 4
    if f.trades_rising:
        entry += 4

    if f.vr < 1.0:
        entry -= 8
    if f.vr5 < 1.0:
        entry -= 6

    if f.ad < 10:
        entry -= 10
    elif f.ad < cfg.min_adx_trend:
        entry -= 2

    if f.trap:
        entry -= 20

    entry = max(0, min(100, entry))
    entry = soft_cap(entry, cfg.entry_soft_cap, cfg.entry_soft_cap_factor)

    return max(0, min(100, int(round(entry))))


def decide_stage(
    cfg: Settings,
    f: Features,
    criteria: Criteria,
    score: int,
    confirmation: int,
) -> str:
    count = criteria.count()

    if f.trap or f.rv >= cfg.rsi_extreme:
        return "NONE"

    # ÖNCÜ AL:
    # 3+ bağımsız kriter, aktivite/momentum tarafında en az bir kanıt.
    activity_or_momentum = (
        criteria.momentum_new
        or criteria.volume_acceleration
        or criteria.trade_acceleration
    )

    if (
        count >= 3
        and activity_or_momentum
        and f.rv < cfg.rsi_overheated
    ):
        stage = "EARLY"
    else:
        stage = "NONE"

    # AL:
    # 4+ kriter + belirgin momentum ve hacim/işlem teyidi.
    momentum_confirmation = (
        criteria.momentum_new
        and (criteria.volume_acceleration or criteria.trade_acceleration)
    )

    if count >= 4 and momentum_confirmation and confirmation >= 12:
        stage = "CONFIRMED"

    # GÜÇLÜ AL:
    # 6+ kriter + güçlü ADX ve kırılım veya çok güçlü confluence.
    strong_trend = (
        f.ad >= cfg.min_adx_trend
        and f.adx_rising
        and f.di_bullish
    )

    strong_confluence = (
        f.vr >= 2
        and f.vr5 >= 1.5
        and f.bp >= 60
        and f.momentum1 > 0
    )

    if (
        count >= 6
        and strong_trend
        and (f.closed_breakout or strong_confluence)
        and not f.trap
        and f.rv < cfg.rsi_extreme
        and confirmation >= 22
    ):
        stage = "VERY"

    return stage


def trend_state_label(
    trend_ok: bool,
    d30: float | None,
    d90: float | None,
    cfg: Settings,
) -> str:
    if not trend_ok:
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

        # Sert düşüş filtresi yalnızca gerçekten negatif ve zayıf hareketi
        # eler; erken yükselişleri engellemez.
        close5 = [float(x[4]) for x in c5]
        volume5 = [float(x[7]) for x in c5]
        price_estimate = close5[-1]

        avg5 = avg(volume5[-12:])
        recent5 = avg(volume5[-3:])
        vr5_early = recent5 / avg5 if avg5 else 0
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

        f = extract_features(cfg, c1, c5)
        criteria = build_criteria(cfg, f)

        setup = score_setup(cfg, f, criteria)

        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0)

        confirmation = score_confirmation(cfg, f, criteria)
        penalty = score_penalty(
            cfg,
            f,
            d30,
            d90,
            market_momentum,
        )

        raw_score = clamp(setup + confirmation + penalty)
        score = clamp(
            int(round(
                soft_cap(
                    raw_score,
                    cfg.score_soft_cap,
                    cfg.score_soft_cap_factor,
                )
            ))
        )

        entry = score_entry_quality(cfg, f, d30, d90)
        stage = decide_stage(
            cfg,
            f,
            criteria,
            score,
            confirmation,
        )

        # Skorun tek başına tier üretmesini engelliyoruz.
        # Tier, bağımsız kriter sayısı + teyit ile belirlenir.
        level = {
            "VERY": "VERY",
            "CONFIRMED": "BUY",
            "EARLY": "EARLY",
        }.get(stage, "PASS")

        # Streak artık kapı değildir.
        qualified = stage in ("EARLY", "CONFIRMED", "VERY") and not f.trap
        streak = db.update_streak(symbol, qualified, f.trap)

        risk_reasons = long_term_risk(d30, d90, cfg)
        criteria_names = criteria.names()

        return {
            "status": level,
            "symbol": symbol,
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
            "old_rsi": f.old_rsi,
            "rsi_rising": f.rsi_rising,

            "ad": f.ad,
            "ad_old": f.ad_old,
            "adx_rising": f.adx_rising,
            "plus_di": f.plus_di,
            "minus_di": f.minus_di,

            "momentum1": f.momentum1,
            "momentum5": f.momentum5,
            "momentum_accel": f.momentum_accel,

            "volume_rising": f.volume_rising,
            "volume_accel_ratio": f.volume_accel_ratio,

            "trades_1m": f.trades1,
            "trades_5m": f.trades5,
            "trades_accel_ratio": f.trades_accel_ratio,
            "trades_rising": f.trades_rising,
            "trade_conf": trade_confidence(cfg, f.trades1, f.vr),

            "ema": f.ema_up,
            "ema_cross": f.ema_cross,
            "macd": f.macd_up,
            "macd_accel": f.macd_accel,

            "squeeze": f.squeeze,
            "expanding": f.expanding,
            "hl": f.higher_low,

            "dist": f.dist,
            "breakout": f.breakout,
            "closed_breakout": f.closed_breakout,

            "criteria_count": criteria.count(),
            "criteria_names": criteria_names,

            "trap": f.trap,
            "trap_reasons": f.trap_reasons,

            "entry_quality": entry,
            "streak": streak,

            "d30": d30,
            "d90": d90,
            "trend_state": trend_state_label(
                trend["ok"],
                d30,
                d90,
                cfg,
            ),
            "long_term_risk": bool(risk_reasons),
            "long_term_risk_reasons": risk_reasons,

            "market_momentum": market_momentum,
            "market_state": market_ctx.get("state", "VERİ YOK"),

            "stage": stage,
        }

    except Exception as e:
        log.exception("%s analiz hatası: %s", symbol, e)
        return {"status": "error", "symbol": symbol}


def priority_score(cfg: Settings, r: dict) -> float:
    value = (
        r["score"] * 0.50
        + r["entry_quality"] * 0.25
        + r["trade_conf"] * 100 * 0.10
    )

    # Kriter sayısı priority'ye küçük bir katkı yapar.
    # Böylece aynı skorda daha fazla bağımsız kanıt öne çıkar.
    value += min(8, r.get("criteria_count", 0) * 1.2)

    if r["streak"] >= 3:
        value += 5
    elif r["streak"] >= 2:
        value += 2

    if r["closed_breakout"]:
        value += 3
    elif r["breakout"]:
        value += 1

    if r["bp"] >= 75:
        value += 4
    elif r["bp"] >= 65:
        value += 2

    if r["vr"] >= 3:
        value += 4
    elif r["vr"] >= 2:
        value += 2
    elif r["vr"] >= 1.5:
        value += 1

    if r["vr5"] >= 2:
        value += 3
    elif r["vr5"] >= 1.5:
        value += 1

    if r["trades_rising"]:
        value += 3

    if r["volume_rising"]:
        value += 2

    if r["ad"] >= cfg.min_adx_trend and r.get("adx_rising"):
        value += 3

    if r["rv"] >= cfg.rsi_extreme:
        value -= 12
    elif r["rv"] >= cfg.rsi_overheated:
        value -= 6

    if r["trap"]:
        value -= 25

    value = max(0, min(100, value))
    value = soft_cap(
        value,
        cfg.priority_soft_cap,
        cfg.priority_soft_cap_factor,
    )

    return max(0, min(100, round(value, 1)))


def rank_signals(cfg: Settings, signals: list[dict]) -> list[dict]:
    for r in signals:
        r["priority"] = priority_score(cfg, r)

    signals.sort(
        key=lambda x: (
            x["priority"],
            x["criteria_count"],
            x["entry_quality"],
            x["score"],
        ),
        reverse=True,
    )

    for i, r in enumerate(signals, 1):
        r["rank"] = i

    return signals
    
