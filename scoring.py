"""
Eski koddaki `analyze()` tek fonksiyonda ~250 satırdı: 5m/1m veri çekme,
indikatör hesaplama, trap tespiti, setup/confirmation/penalty/entry
puanlama ve stage kararı hepsi iç içeydi. Sonuç: hiçbir alt parça tek
başına test edilemiyordu.

Burada aynı MANTIK korunarak (davranış değiştirilmedi) adımlar ayrı, saf
fonksiyonlara bölündü. `analyze()` artık sadece bu adımları sırayla
çağıran bir orkestratör -- indikatör hesaplama, puanlama ve stage kararı
birbirinden bağımsız test edilebilir.
"""

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


def long_term_penalty(cfg: Settings, d30: float, d90: float) -> int:
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


def trade_confidence(cfg: Settings, trades: int, volume_ratio: float) -> float:
    if trades <= 0:
        return 0
    if volume_ratio >= 2 and trades < cfg.min_1m_trades:
        return 0.25
    if trades < cfg.min_1m_trades:
        return 0.40
    return min(1.0, max(0.40, trades / cfg.trade_reference))


def extract_features(cfg: Settings, c1: list, close5: list[float], volume5: list[float],
                      price_5m_close: float, trades5_sum: int) -> Features:
    """1m kline'lardan tüm türetilmiş metrikleri ve indikatörleri hesaplar."""
    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]
    trades = [int(x[8]) for x in c1]

    price = close[-1]

    avg5 = avg(volume5[-12:])
    recent5 = avg(volume5[-3:])
    vr5 = recent5 / avg5 if avg5 else 0
    momentum5 = pct(close5[-4], price)

    momentum1 = pct(close[-2], price)

    low90 = min(low[-90:])
    high90 = max(high[-90:])
    location = (price - low90) / (high90 - low90) * 100 if high90 > low90 else 50

    avg_volume = avg(volume[-30:])
    last3 = avg(volume[-3:])
    previous = avg(volume[-10:-3])
    vr = last3 / avg_volume if avg_volume else 0
    impulse = min(last3 / previous if previous else 1, 10)

    buy_volume = sum(float(x[10]) for x in c1[-5:])
    total_volume = sum(float(x[7]) for x in c1[-5:])
    bp = buy_volume / total_volume * 100 if total_volume else 50

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
    width = (upper - lower) / middle * 100 if middle else 0
    old_lower, old_middle, old_upper = bb(close[:-5])
    old_width = (old_upper - old_lower) / old_middle * 100 if old_middle else width

    squeeze = width <= 2.2 or (old_width > 0 and width < old_width * 0.80)
    expanding = old_width > 0 and width > old_width * 1.08

    resistance = max(high[-30:-2])
    dist = max(0, (resistance - price) / price * 100)
    breakout = price > resistance
    closed_breakout = close[-1] > resistance

    higher_low = low[-1] > low[-3] and low[-3] >= low[-6]

    candle_range = high[-1] - low[-1]
    close_position = (close[-1] - low[-1]) / candle_range * 100 if candle_range > 0 else 50

    low_activity = trades1 < cfg.min_1m_trades or trades5_sum < cfg.min_5m_trades
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
        price=price, momentum1=momentum1, momentum5=momentum5, location=location,
        close_position=close_position, vr=vr, vr5=vr5, impulse=impulse, bp=bp, trades1=trades1, trades5=trades5_sum,
        ema_up=ema_up, ema_cross=ema_cross, price_above_ema50=price >= ema50,
        rv=rv, old_rsi=old_rsi, macd_up=macd_up, ad=ad, plus_di=plus_di, minus_di=minus_di,
        squeeze=squeeze, expanding=expanding, dist=dist, breakout=breakout,
        closed_breakout=closed_breakout, higher_low=higher_low,
        low_activity=low_activity, weak_volume=weak_volume,
        trap=bool(trap_reasons), trap_reasons=trap_reasons,
    )


def score_setup(cfg: Settings, f: Features) -> int:
    setup = 0
    if f.ema_up:
        setup += 12
    if f.ema_cross:
        setup += 6
    if f.squeeze:
        setup += 8
    if f.higher_low:
        setup += 6
    if 35 <= f.rv <= 65 and f.rv > f.old_rsi:
        setup += 8
    if f.price_above_ema50:
        setup += 5
    if f.dist <= 0.70:
        setup += 8
    if f.vr >= 1.5 and f.trades1 >= cfg.min_1m_trades:
        setup += 8
    if f.bp >= 58 and f.trades1 >= cfg.min_1m_trades:
        setup += 5
    return setup


def score_confirmation(cfg: Settings, f: Features) -> int:
    confirmation = 0
    if f.closed_breakout:
        confirmation += 18
    elif f.breakout:
        confirmation += 10

    if f.vr >= 2:
        confirmation += 12
    elif f.vr >= 1.5:
        confirmation += 7

    if f.vr5 >= 1.5:
        confirmation += 8

    if f.bp >= 65 and f.trades1 >= cfg.min_1m_trades:
        confirmation += 7

    if f.macd_up:
        confirmation += 6

    if f.plus_di > f.minus_di and f.ad >= 18:
        confirmation += 7

    if f.close_position >= 65:
        confirmation += 4

    if f.expanding:
        confirmation += 4

    if f.trades1 >= cfg.min_1m_trades and f.trades5 >= cfg.min_5m_trades:
        confirmation += 3

    if f.weak_volume:
        confirmation -= 8
    if f.ad < 10:
        confirmation -= 10
    if f.low_activity:
        confirmation -= 8

    return confirmation


def score_penalty(cfg: Settings, f: Features, d30: float | None, d90: float | None,
                   market_momentum: float) -> int:
    penalty = long_term_penalty(cfg, d30, d90) if d30 is not None and d90 is not None else -5

    if f.momentum1 > 2.5:
        penalty -= 10
    if f.momentum5 > 5:
        penalty -= 12
    if f.rv > 78:
        penalty -= 10
    if f.rv >= 85:
        penalty -= 8
    if f.bp < 50 and f.vr >= 1.8:
        penalty -= 8
    if f.momentum5 < -1.2 and not f.higher_low:
        penalty -= 12
    if f.vr >= 2 and f.trades1 < cfg.min_1m_trades:
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


def score_entry_quality(cfg: Settings, f: Features, d30: float | None, d90: float | None) -> int:
    entry = 100

    if f.rv >= 85:
        entry -= 30
    elif f.rv >= 78:
        entry -= 15

    if f.momentum1 >= 5:
        entry -= 25
    elif f.momentum1 >= 2.5:
        entry -= 12

    if f.momentum5 >= 5:
        entry -= 20
    elif f.momentum5 >= 3:
        entry -= 10

    if f.dist <= 0.15:
        entry -= 8
    elif f.dist <= 0.35:
        entry -= 4

    if f.closed_breakout:
        entry += 5
    if f.higher_low:
        entry += 5

    if f.trades1 < cfg.min_1m_trades:
        entry -= 18
    if f.trades1 < 5:
        entry -= 15

    if f.vr < 1.0:
        entry -= 12
    if f.vr5 < 1.0:
        entry -= 10
    if f.ad < 10:
        entry -= 12

    if d30 is not None and d30 >= 20:
        entry -= 5
    if d90 is not None and d90 <= cfg.lt90_strong:
        entry -= 10

    if f.trap:
        entry -= 20

    return max(0, min(100, int(round(entry))))


def decide_stage(cfg: Settings, f: Features, score: int, setup: int, confirmation: int,
                  d30: float | None, d90: float | None) -> str:
    stage = "NONE"

    if setup >= 25:
        stage = "SETUP"

    if score >= 68 and confirmation >= 18 and not f.weak_volume:
        stage = "CONFIRMED"

    very_ok = (
        d30 is not None and d90 is not None
        and d30 > cfg.lt30_strong and d90 > cfg.lt90_strong
        and not f.low_activity and not f.weak_volume
        and f.ad >= 18 and f.rv < 85 and not f.trap
    )

    if (score >= 84 and confirmation >= 28 and f.vr >= 1.5 and f.vr5 >= 1.0
            and very_ok and f.closed_breakout):
        stage = "VERY"

    return stage


def trend_state_label(trend_ok: bool, d30: float | None, d90: float | None, cfg: Settings) -> str:
    if not trend_ok:
        return "VERİ YOK"
    if d30 > 10 and d90 > 0:
        return "POZİTİF TREND"
    if d90 <= cfg.lt90_extreme or d30 <= cfg.lt30_strong:
        return "YÜKSEK DÜŞÜŞ RİSKİ"
    if d90 <= cfg.lt90_strong or d30 <= cfg.lt30_mild:
        return "DÜŞÜŞ RİSKİ"
    return "NÖTR"


def analyze(cfg: Settings, client: BinanceClient, db: DB, market: MarketData, item: dict) -> dict:
    """Orkestratör: veri çeker, özellik çıkarır, puanlar, stage'e karar verir."""
    symbol = item["symbol"]

    try:
        k5 = client.klines(symbol, "5m", 80)
        if len(k5) < 40:
            return {"status": "PASS"}

        c5 = k5[:-1]
        close5 = [float(x[4]) for x in c5]
        volume5 = [float(x[7]) for x in c5]
        trades5_list = [int(x[8]) for x in c5]

        price_estimate = close5[-1]
        avg5 = avg(volume5[-12:])
        recent5 = avg(volume5[-3:])
        vr5_early = recent5 / avg5 if avg5 else 0
        momentum5_early = pct(close5[-4], price_estimate)

        if momentum5_early < -3 and vr5_early < 1.3:
            return {"status": "PASS"}

        trend = market.daily_trend(symbol)
        d30 = trend["d30"] if trend["ok"] else None
        d90 = trend["d90"] if trend["ok"] else None

        k1 = client.klines(symbol, "1m", 180)
        if len(k1) < 100:
            return {"status": "PASS"}

        c1 = k1[:-1]
        trades5_sum = sum(trades5_list[-1:])

        f = extract_features(cfg, c1, close5, volume5, price_estimate, trades5_sum)

        setup = score_setup(cfg, f)
        confirmation = score_confirmation(cfg, f)
        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0)
        penalty = score_penalty(cfg, f, d30, d90, market_momentum)

        score = clamp(setup + confirmation + penalty)

        if f.low_activity:
            score = min(score, 78)
        if f.weak_volume:
            score = min(score, 82)
        if f.ad < 10:
            score = min(score, 72)
        if f.rv >= 90 and f.trades1 < cfg.min_1m_trades:
            score = min(score, 65)
        if d90 is not None and d90 <= cfg.lt90_extreme:
            score = min(score, 82)

        entry = score_entry_quality(cfg, f, d30, d90)
        stage = decide_stage(cfg, f, score, setup, confirmation, d30, d90)

        level = {"VERY": "VERY", "CONFIRMED": "BUY", "SETUP": "INTERNAL"}.get(stage, "PASS")
        qualified = stage in ("SETUP", "CONFIRMED", "VERY") and not f.trap

        streak = db.update_streak(symbol, qualified, f.trap)

        if level == "BUY" and streak < cfg.buy_streak:
            level = "INTERNAL"
        if level == "VERY" and streak < cfg.very_streak:
            level = "INTERNAL"

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
        }

    except Exception as e:
        log.debug("%s: %s", symbol, e)
        return {"status": "error", "symbol": symbol}


def priority_score(cfg: Settings, r: dict) -> float:
    value = (
        r["score"] * 0.50
        + r["entry_quality"] * 0.25
        + r["trade_conf"] * 100 * 0.10
    )

    if r["streak"] >= 3:
        value += 8
    elif r["streak"] >= 2:
        value += 4

    if r["closed_breakout"]:
        value += 8
    elif r["breakout"]:
        value += 2

    if r["bp"] >= 75:
        value += 5
    elif r["bp"] >= 65:
        value += 3

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

    if r["vr5"] < 0.75:
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

    signals.sort(key=lambda x: (x["priority"], x["entry_quality"], x["score"]), reverse=True)

    for i, r in enumerate(signals, 1):
        r["rank"] = i

    return signals
  
