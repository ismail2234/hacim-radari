from __future__ import annotations

import logging
from dataclasses import dataclass

from binance_client import BinanceClient
from config import Settings
from db import DB
from indicators import (
    avg, pct, ema, rsi, macd, adx,
    bb, atr, obv, vwap
)
from market import MarketData

log = logging.getLogger("v26.scoring")


@dataclass
class Features:
    price: float
    ma7: float
    ma30: float
    ma99: float

    rsi: float
    rsi_old: float

    macd: float
    macd_old: float

    k: float
    d: float
    j: float

    adx: float
    plus_di: float
    minus_di: float

    volume_ratio: float
    volume5_ratio: float
    buy_pressure: float

    momentum1: float
    momentum5: float

    resistance: float
    distance: float

    squeeze: bool
    expanding: bool
    breakout: bool
    closed_breakout: bool

    candle_body: float
    close_position: float

    higher_low: bool
    price_above_vwap: bool

    obv_rising: bool

    trap: bool
    trap_reasons: list[str]


def kdj(highs, lows, closes, period=9):
    if len(closes) < period:
        return 50.0, 50.0, 50.0

    values = []

    for i in range(period - 1, len(closes)):
        hi = max(highs[i - period + 1:i + 1])
        lo = min(lows[i - period + 1:i + 1])

        if hi == lo:
            values.append(50.0)
        else:
            values.append(
                (closes[i] - lo) /
                (hi - lo) * 100
            )

    if not values:
        return 50.0, 50.0, 50.0

    k = 50.0
    d = 50.0

    for x in values[-20:]:
        k = (2 * k + x) / 3
        d = (2 * d + k) / 3

    j = 3 * k - 2 * d

    return k, d, j


def extract_features(cfg, c1, c5):
    close = [float(x[4]) for x in c1]
    high = [float(x[2]) for x in c1]
    low = [float(x[3]) for x in c1]
    volume = [float(x[7]) for x in c1]

    if len(close) < 110:
        return None

    price = close[-1]

    ma7 = ema(close, 7)
    ma30 = ema(close, 30)
    ma99 = ema(close, 99)

    rv = rsi(close)
    old_rsi = rsi(close[:-3])

    _, _, macd_now = macd(close)
    _, _, macd_old = macd(close[:-3])

    ad, plus, minus = adx(high, low, close)

    k, d, j = kdj(high, low, close)

    avg_volume = avg(volume[-30:])
    recent_volume = avg(volume[-3:])

    volume_ratio = (
        recent_volume / avg_volume
        if avg_volume else 0
    )

    volume5 = [float(x[7]) for x in c5]

    avg_v5 = avg(volume5[-12:])
    recent_v5 = avg(volume5[-3:])

    volume5_ratio = (
        recent_v5 / avg_v5
        if avg_v5 else 0
    )

    buy = sum(float(x[10]) for x in c1[-5:])
    total = sum(float(x[7]) for x in c1[-5:])

    buy_pressure = (
        buy / total * 100
        if total else 50
    )

    momentum1 = pct(close[-2], close[-1])
    momentum5 = (
        pct(close[-6], close[-1])
        if len(close) > 6 else 0
    )

    resistance = max(high[-32:-2])

    distance = max(
        0,
        (resistance - price) /
        price * 100
    )

    breakout = price > resistance

    # c1 zaten KAPANMIŞ mumlardan oluşuyor.
    closed_breakout = close[-1] > resistance

    candle_range = high[-1] - low[-1]

    body = (
        abs(close[-1] - close[-2]) /
        close[-2] * 100
        if close[-2] else 0
    )

    close_position = (
        (close[-1] - low[-1]) /
        candle_range * 100
        if candle_range else 50
    )

    lower, middle, upper = bb(close)

    width = (
        (upper - lower) /
        middle * 100
        if middle else 0
    )

    old_l, old_m, old_u = bb(close[:-5])

    old_width = (
        (old_u - old_l) /
        old_m * 100
        if old_m else width
    )

    squeeze = (
        width <= 2.5 or
        (
            old_width > 0 and
            width < old_width * 0.80
        )
    )

    expanding = (
        old_width > 0 and
        width > old_width * 1.08
    )

    higher_low = (
        low[-1] > low[-4] and
        low[-4] >= low[-7]
    )

    obv_data = obv(close, volume)

    obv_rising = (
        len(obv_data) >= 20 and
        obv_data[-1] > obv_data[-20]
    )

    vw = vwap(
        high,
        low,
        close,
        volume
    )

    above_vwap = (
        vw > 0 and
        price > vw
    )

    traps = []

    if buy_pressure < 50 and volume_ratio >= 2:
        traps.append("zayıf alıcı")

    if momentum5 < -1.5:
        traps.append("negatif momentum")

    if volume_ratio >= 2 and buy_pressure < 45:
        traps.append("hacim tuzağı")

    if rv >= 88 and momentum1 > 4:
        traps.append("geç hareket")

    trap = bool(traps)

    return Features(
        price=price,
        ma7=ma7,
        ma30=ma30,
        ma99=ma99,
        rsi=rv,
        rsi_old=old_rsi,
        macd=macd_now,
        macd_old=macd_old,
        k=k,
        d=d,
        j=j,
        adx=ad,
        plus_di=plus,
        minus_di=minus,
        volume_ratio=volume_ratio,
        volume5_ratio=volume5_ratio,
        buy_pressure=buy_pressure,
        momentum1=momentum1,
        momentum5=momentum5,
        resistance=resistance,
        distance=distance,
        squeeze=squeeze,
        expanding=expanding,
        breakout=breakout,
        closed_breakout=closed_breakout,
        candle_body=body,
        close_position=close_position,
        higher_low=higher_low,
        price_above_vwap=above_vwap,
        obv_rising=obv_rising,
        trap=trap,
        trap_reasons=traps,
    )


def score_v26(f: Features, market_momentum: float):
    score = 0
    reasons = []

    # 1 — Konsolidasyon
    if f.squeeze:
        score += 12
        reasons.append("Sıkışma")

    # 2 — MA yapısı
    if f.ma7 > f.ma30:
        score += 8
        reasons.append("MA7>MA30")

    if f.ma30 > f.ma99:
        score += 8
        reasons.append("MA30>MA99")

    # 3 — Fiyat MA7 üzerinde
    if f.price > f.ma7:
        score += 5
        reasons.append("MA7 üstü")

    # 4 — Hacim
    if f.volume_ratio >= 3:
        score += 15
        reasons.append("Hacim 3x+")

    elif f.volume_ratio >= 2:
        score += 11
        reasons.append("Hacim 2x+")

    elif f.volume_ratio >= 1.5:
        score += 6

    # 5 — 5m hacim
    if f.volume5_ratio >= 2:
        score += 7
        reasons.append("5m hacim")

    elif f.volume5_ratio >= 1.5:
        score += 4

    # 6 — Alıcı baskısı
    if f.buy_pressure >= 70:
        score += 10
        reasons.append("Alıcı baskısı")

    elif f.buy_pressure >= 60:
        score += 6

    # 7 — RSI
    if 50 <= f.rsi <= 70 and f.rsi > f.rsi_old:
        score += 8
        reasons.append("RSI yükseliyor")

    elif 45 <= f.rsi <= 75:
        score += 3

    # 8 — MACD
    if f.macd > f.macd_old and f.macd > 0:
        score += 8
        reasons.append("MACD teyit")

    elif f.macd > f.macd_old:
        score += 4

    # 9 — KDJ
    if f.k > f.d and f.j > f.k:
        score += 8
        reasons.append("KDJ yükseliş")

    # 10 — ADX
    if f.adx >= 25 and f.plus_di > f.minus_di:
        score += 8
        reasons.append("ADX güçlü")

    elif f.plus_di > f.minus_di:
        score += 3

    # 11 — Breakout
    if f.closed_breakout:
        score += 12
        reasons.append("KAPANMIŞ MUM KIRILIM")

    elif f.breakout:
        score += 4

    # 12 — Mum kalitesi
    if f.close_position >= 70:
        score += 5
        reasons.append("Güçlü kapanış")

    # 13 — Higher low
    if f.higher_low:
        score += 4

    # 14 — VWAP
    if f.price_above_vwap:
        score += 3

    # 15 — OBV
    if f.obv_rising:
        score += 4

    # 16 — Kırılımdan fazla uzaklaşmışsa ceza
    if f.distance > 3:
        score -= 8

    if f.distance > 5:
        score -= 8

    # 17 — Aşırı ısınma
    if f.rsi >= 80:
        score -= 12

    if f.momentum1 >= 5:
        score -= 10

    # 18 — BTC filtresi
    if market_momentum <= -3:
        score -= 15

    elif market_momentum <= -1.5:
        score -= 8

    elif market_momentum >= 2:
        score += 3

    # 19 — Trap
    if f.trap:
        score -= 20

    return max(0, min(100, score)), reasonsdef decide_stage(f: Features, score: int, cfg: Settings) -> str:
    if f.trap:
        return "PASS"

    # ÖNCÜ: hareket oluşmaya başlıyor ama henüz tam teyit yok
    early = 0

    if f.squeeze:
        early += 1
    if f.ma7 > f.ma30:
        early += 1
    if f.volume_ratio >= 1.5:
        early += 1
    if f.rsi > f.rsi_old:
        early += 1
    if f.macd > f.macd_old:
        early += 1
    if f.k > f.d:
        early += 1
    if f.obv_rising:
        early += 1

    if score >= 80 and early >= 5:
        # Gerçek AL için güçlü teyit şart
        if (
            f.closed_breakout
            and f.volume_ratio >= 1.5
            and f.buy_pressure >= 55
            and f.rsi < 80
        ):
            return "BUY"

    if score >= 68 and early >= 4:
        return "ONCU"

    if score >= 55 and early >= 3:
        return "WATCH"

    return "PASS"


def priority_score(f: Features, score: int) -> float:
    value = float(score)

    if f.closed_breakout:
        value += 6

    if f.volume_ratio >= 2:
        value += 5

    if f.buy_pressure >= 65:
        value += 4

    if f.adx >= 25:
        value += 4

    if f.price_above_vwap:
        value += 2

    if f.k > f.d:
        value += 2

    if f.trap:
        value -= 20

    return max(0, min(100, round(value, 1)))


def analyze(
    cfg: Settings,
    client: BinanceClient,
    db: DB,
    market: MarketData,
    item: dict
) -> dict:

    symbol = item["symbol"]

    try:
        k5 = client.klines(symbol, "5m", 80)

        if len(k5) < 40:
            return {"status": "PASS"}

        c5 = k5[:-1]

        k1 = client.klines(symbol, "1m", 180)

        if len(k1) < 110:
            return {"status": "PASS"}

        c1 = k1[:-1]

        f = extract_features(cfg, c1, c5)

        if f is None:
            return {"status": "PASS"}

        market_ctx = market.context()
        market_momentum = market_ctx.get("momentum", 0)

        score, reasons = score_v26(
            f,
            market_momentum
        )

        stage = decide_stage(
            f,
            score,
            cfg
        )

        priority = priority_score(
            f,
            score
        )

        qualified = stage in (
            "ONCU",
            "BUY"
        )

        streak = db.update_streak(
            symbol,
            qualified,
            f.trap
        )

        return {
            "status": stage,
            "symbol": symbol,

            "score": score,
            "priority": priority,

            "price": f.price,

            "chg": item.get("chg", 0),

            "ma7": f.ma7,
            "ma30": f.ma30,
            "ma99": f.ma99,

            "rsi": f.rsi,
            "macd": f.macd,

            "k": f.k,
            "d": f.d,
            "j": f.j,

            "adx": f.adx,
            "plus_di": f.plus_di,
            "minus_di": f.minus_di,

            "volume_ratio": f.volume_ratio,
            "volume5_ratio": f.volume5_ratio,
            "buy_pressure": f.buy_pressure,

            "momentum1": f.momentum1,
            "momentum5": f.momentum5,

            "resistance": f.resistance,
            "distance": f.distance,

            "squeeze": f.squeeze,
            "expanding": f.expanding,

            "breakout": f.breakout,
            "closed_breakout": f.closed_breakout,

            "close_position": f.close_position,
            "higher_low": f.higher_low,

            "price_above_vwap": f.price_above_vwap,
            "obv_rising": f.obv_rising,

            "trap": f.trap,
            "trap_reasons": f.trap_reasons,

            "streak": streak,

            "market_momentum": market_momentum,
            "market_state": market_ctx.get(
                "state",
                "VERİ YOK"
            ),

            "reasons": reasons,
        }

    except Exception as e:
        log.warning(
            "%s V26 analiz hatası: %s",
            symbol,
            e,
            exc_info=True
        )

        return {
            "status": "error",
            "symbol": symbol
        }


def rank_signals(
    cfg: Settings,
    signals: list[dict]
) -> list[dict]:

    signals.sort(
        key=lambda x: (
            x.get("priority", 0),
            x.get("score", 0)
        ),
        reverse=True
    )

    for i, signal in enumerate(
        signals,
        1
    ):
        signal["rank"] = i

    return signals
