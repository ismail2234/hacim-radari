from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from binance_client import BinanceClient
from config import Settings
from db import DB
from indicators import (
    adx,
    avg,
    bb,
    ema,
    macd,
    pct,
    rsi,
    vwap,
)
from market import MarketData

log = logging.getLogger("balina.scoring")


@dataclass
class Features:
    price: float = 0.0

    # ---------------------------------------------------------
    # MOMENTUM
    # ---------------------------------------------------------

    momentum1: float = 0.0
    momentum5: float = 0.0
    rv: float = 50.0
    old_rsi: float = 50.0

    # ---------------------------------------------------------
    # HAREKETLİ ORTALAMALAR
    # ---------------------------------------------------------

    ma7: float = 0.0
    ma30: float = 0.0
    ma99: float = 0.0

    ma7_old: float = 0.0
    ma30_old: float = 0.0

    ma7_cross: bool = False
    ma_structure: bool = False

    # ---------------------------------------------------------
    # DARALMA
    # ---------------------------------------------------------

    consolidation: bool = False
    consolidation_range: float = 0.0
    bb_width: float = 0.0
    squeeze: bool = False

    # ---------------------------------------------------------
    # KIRILIM
    # ---------------------------------------------------------

    resistance: float = 0.0
    dist: float = 0.0
    breakout: bool = False
    closed_breakout: bool = False

    # ---------------------------------------------------------
    # MUM
    # ---------------------------------------------------------

    close_position: float = 50.0
    upper_wick_pct: float = 0.0
    strong_close: bool = False

    # ---------------------------------------------------------
    # HACİM
    # ---------------------------------------------------------

    vr: float = 0.0
    vr5: float = 0.0
    impulse: float = 1.0

    # ---------------------------------------------------------
    # ALICI BASKISI
    # ---------------------------------------------------------

    bp: float = 50.0

    # ---------------------------------------------------------
    # İŞLEM SAYISI
    # ---------------------------------------------------------

    trades1: int = 0
    trades5: int = 0

    # ---------------------------------------------------------
    # TEKNİK TEYİT
    # ---------------------------------------------------------

    macd_up: bool = False

    ad: float = 0.0
    plus_di: float = 0.0
    minus_di: float = 0.0
    adx_rising: bool = False

    # ---------------------------------------------------------
    # FİYAT YAPISI
    # ---------------------------------------------------------

    higher_low: bool = False

    # ---------------------------------------------------------
    # VWAP
    # ---------------------------------------------------------

    price_above_vwap: bool = False
    vwap_value: float = 0.0

    # ---------------------------------------------------------
    # FAKEOUT
    # ---------------------------------------------------------

    fakeout: bool = False
    fakeout_reasons: list[str] = field(default_factory=list)

    # ---------------------------------------------------------
    # TUZAK
    # ---------------------------------------------------------

    trap: bool = False
    trap_reasons: list[str] = field(default_factory=list)

    # ---------------------------------------------------------
    # OPEN INTEREST
    # ---------------------------------------------------------

    oi_available: bool = False
    oi_change: float | None = None


def safe_float(value, default: float = 0.0) -> float:
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
    Uzun süre dar bantta hareket eden ve volatilitesi
    düşen coinleri kırılım öncesinde tespit etmek.
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

    lower, middle, upper = bb(c, 20, 2)

    if middle > 0:
        bb_width = (
            (upper - lower)
            / middle
            * 100
        )
    else:
        bb_width = 0.0

    range_pct = (
        (high_value - low_value)
        / price
        * 100
    )

    range_ok = (
        range_pct
        <= cfg.consolidation_max_range
    )

    bb_ok = (
        bb_width
        <= cfg.consolidation_max_bb_width
    )

    return (
        range_ok and bb_ok,
        range_pct,
        bb_width,
    )


def calculate_features(
    cfg: Settings,
    c1: list,
    close5: list[float],
    volume5: list[float],
    trades5_sum: int,
) -> Features:

    if len(c1) < 100:
        raise ValueError(
            "1m veri yetersiz"
        )

    close = [
        safe_float(x[4])
        for x in c1
    ]

    high = [
        safe_float(x[2])
        for x in c1
    ]

    low = [
        safe_float(x[3])
        for x in c1
    ]

    volume = [
        safe_float(x[7])
        for x in c1
    ]

    trades = [
        int(safe_float(x[8]))
        for x in c1
    ]

    price = close[-1]

    # ---------------------------------------------------------
    # MA7 / MA30 / MA99
    # ---------------------------------------------------------

    ma7 = ema(
        close,
        cfg.ma_fast,
    )

    ma30 = ema(
        close,
        cfg.ma_mid,
    )

    ma99 = ema(
        close,
        cfg.ma_slow,
    )

    ma7_old = ema(
        close[:-2],
        cfg.ma_fast,
    )

    ma30_old = ema(
        close[:-2],
        cfg.ma_mid,
    )

    # MA7'nin MA30'u yeni yukarı kesmesi
    ma7_cross = (
        ma7 > ma30
        and ma7_old <= ma30_old
        and (
            cfg.ma7_break_pct <= 0
            or ma7
            >= ma30
            * (
                1
                + cfg.ma7_break_pct / 100
            )
        )
    )

    # Ana trend yapısı
    ma_structure = (
        price > ma7
        and ma7 > ma30
        and ma30 > ma99
    )

    # ---------------------------------------------------------
    # MOMENTUM
    # ---------------------------------------------------------

    momentum1 = pct(
        close[-2],
        price,
    )

    momentum5 = (
        pct(
            close5[-4],
            price,
        )
        if len(close5) >= 4
        else 0.0
    )

    current_rsi = rsi(close)

    old_rsi = rsi(
        close[:-3]
    )

    # ---------------------------------------------------------
    # MACD
    # ---------------------------------------------------------

    _, _, macd_now = macd(
        close
    )

    _, _, macd_old = macd(
        close[:-3]
    )

    macd_up = (
        macd_now > macd_old
    )

    # ---------------------------------------------------------
    # ADX
    # ---------------------------------------------------------

    ad, plus_di, minus_di = adx(
        high,
        low,
        close,
    )

    if len(close) > 40:

        ad_prev, _, _ = adx(
            high[:-3],
            low[:-3],
            close[:-3],
        )

    else:
        ad_prev = ad

    adx_rising = (
        ad > ad_prev
    )

    # ---------------------------------------------------------
    # HACİM
    # ---------------------------------------------------------

    avg_volume = avg(
        volume[-30:]
    )

    recent_volume = avg(
        volume[-3:]
    )

    vr = (
        recent_volume
        / avg_volume
        if avg_volume
        else 0.0
    )

    avg5 = avg(
        volume5[-12:]
    )

    recent5 = avg(
        volume5[-3:]
    )

    vr5 = (
        recent5
        / avg5
        if avg5
        else 0.0
    )

    previous_volume = avg(
        volume[-10:-3]
    )

    impulse = (
        recent_volume
        / previous_volume
        if previous_volume
        else 1.0
    )

    impulse = min(
        impulse,
        10.0,
    )

    # ---------------------------------------------------------
    # ALICI BASKISI
    #
    # Binance kline:
    # [10] = taker buy quote volume
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
        buy_volume
        / total_volume
        * 100
        if total_volume
        else 50.0
    )

    # ---------------------------------------------------------
    # İŞLEM SAYILARI
    # ---------------------------------------------------------

    trades1 = sum(
        trades[-5:]
    )

    trades5 = trades5_sum

    # ---------------------------------------------------------
    # DARALMA
    # ---------------------------------------------------------

    (
        consolidation,
        range_pct,
        bb_width,
    ) = calculate_consolidation(
        close,
        high,
        low,
        cfg,
    )

    squeeze = consolidation

    # ---------------------------------------------------------
    # DİRENÇ / KIRILIM
    # ---------------------------------------------------------

    lookback = (
        cfg.breakout_lookback
    )

    resistance_slice = high[
        -(lookback + 2):-2
    ]

    resistance = (
        max(resistance_slice)
        if resistance_slice
        else price
    )

    dist = (
        max(
            0.0,
            (
                resistance
                - price
            )
            / price
            * 100,
        )
        if price > 0
        else 0.0
    )

    breakout = (
        price
        > resistance
        * (
            1
            + cfg.breakout_buffer
            / 100
        )
    )

    # Burada c1 yalnızca kapanmış mumları içeriyor.
    # Dolayısıyla breakout doğruysa kapanmış mum
    # kırılımı da teyit edilmiş kabul edilir.
    closed_breakout = (
        breakout
        if cfg.require_closed_breakout
        else breakout
    )

    # ---------------------------------------------------------
    # MUM YAPISI
    # ---------------------------------------------------------

    candle_high = high[-1]
    candle_low = low[-1]
    candle_close = close[-1]

    candle_open = safe_float(
        c1[-1][1]
    )

    candle_range = (
        candle_high
        - candle_low
    )

    if candle_range > 0:

        close_position = (
            (
                candle_close
                - candle_low
            )
            / candle_range
            * 100
        )

        upper_wick = (
            candle_high
            - max(
                candle_open,
                candle_close,
            )
        )

        upper_wick_pct = (
            upper_wick
            / candle_range
            * 100
        )

    else:

        close_position = 50.0
        upper_wick_pct = 0.0

    strong_close = (
        close_position
        >= cfg.fakeout_min_close_position
    )

    # ---------------------------------------------------------
    # HIGHER LOW
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
        ma30_old=ma30_old,
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
    )
