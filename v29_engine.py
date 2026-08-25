from __future__ import annotations

from typing import Any

import pandas as pd

from indicators import add_indicators
from signal_engine import calculate_signal as calculate_v28_signal
from volume_engine import calculate_volume_metrics, detect_volume_acceleration


def _clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if pd.notna(x) else default
    except (TypeError, ValueError):
        return default


def _add_v29_indicators(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()

    # Session-independent rolling VWAP for the available scan window.
    typical = (data["high"] + data["low"] + data["close"]) / 3.0
    vol = pd.to_numeric(data["volume"], errors="coerce").fillna(0)

    cumulative_vol = vol.cumsum()

    # FutureWarning düzeltildi:
    # pd.NA yerine float("nan") kullanılıyor.
    data["vwap"] = (
        (typical * vol).cumsum()
        / cumulative_vol.replace(0, float("nan"))
    ).fillna(data["close"])

    # TD Sequential-style setup count.
    # Buy setup: close < close 4 bars earlier.
    buy = data["close"] < data["close"].shift(4)

    counts = []
    count = 0

    for ok in buy.fillna(False):
        count = count + 1 if ok else 0
        counts.append(count)

    data["td_buy"] = counts
    data["td13"] = data["td_buy"] >= 13

    return data


def _ema7_quality(data: pd.DataFrame) -> tuple[int, list[str]]:
    if len(data) < 6:
        return 0, []

    ema7 = data["ema_7"].astype(float)

    s_now = _f(ema7.iloc[-1] - ema7.iloc[-4])
    s_prev = _f(ema7.iloc[-2] - ema7.iloc[-5])

    score = 0
    reasons = []

    if s_now > 0:
        score += 15
        reasons.append("EMA7 eğimi pozitif")
    elif s_now >= s_prev:
        score += 10
        reasons.append("EMA7 eğimi düzeliyor")

    if s_now > s_prev:
        score += 10
        reasons.append("EMA7 ivmesi")

    return score, reasons


def _higher_low(data: pd.DataFrame) -> tuple[bool, int]:
    if len(data) < 12:
        return False, 0

    recent = _f(data["low"].iloc[-5:].min())
    previous = _f(data["low"].iloc[-10:-5].min())

    if previous <= 0:
        return False, 0

    delta = (recent - previous) / previous * 100

    return delta > 0, _clamp(delta * 40)


def _selling_rejection(data: pd.DataFrame) -> bool:
    if data.empty:
        return False

    last = data.iloc[-1]

    candle_range = _f(last["high"] - last["low"])

    if candle_range <= 0:
        return False

    lower_wick = _f(
        min(last["open"], last["close"]) - last["low"]
    )

    close_position = _f(
        (last["close"] - last["low"]) / candle_range
    )

    return (
        lower_wick / candle_range >= 0.35
        and close_position >= 0.55
    )


def _absorption_score(
    data: pd.DataFrame,
    volume_ratio: float,
) -> int:
    if volume_ratio < 1.3:
        return 0

    if not _selling_rejection(data):
        return 0

    if volume_ratio >= 2.0:
        return 100

    if volume_ratio >= 1.5:
        return 75

    return 50


def _curve_score(
    data: pd.DataFrame,
    volume_ratio: float,
    volume_acceleration: bool,
) -> tuple[int, list[str]]:

    last = data.iloc[-1]
    prev = data.iloc[-2]

    score = 0
    reasons = []

    if bool(last.get("near_dip", False)):
        score += 12
        reasons.append("Dip bölgesi")

    hl, hl_quality = _higher_low(data)

    if bool(last.get("higher_low", False)) or hl:
        score += 15
        reasons.append("Higher-Low")

    ema_score, ema_reasons = _ema7_quality(data)

    score += ema_score
    reasons.extend(ema_reasons)

    rsi = _f(last["rsi_14"])
    prev_rsi = _f(prev["rsi_14"])

    if rsi > prev_rsi:
        score += 10
        reasons.append("RSI yükseliyor")

    if prev_rsi < 45 <= rsi:
        score += 10
        reasons.append("RSI dönüşü")

    if 40 <= rsi <= 55:
        score += 6
        reasons.append("RSI erken bölge")

    if _selling_rejection(data):
        score += 10
        reasons.append("Dipte satış reddi")

    if volume_ratio >= 1.5:
        score += 8
        reasons.append("İlk hacim")

    if volume_acceleration:
        score += 7
        reasons.append("Hacim ivmesi")

    return _clamp(score), list(dict.fromkeys(reasons))


def _earlyness_score(
    data: pd.DataFrame,
    volume_ratio: float,
) -> int:

    last = data.iloc[-1]

    close = _f(last["close"])
    ema7 = _f(last["ema_7"])
    rsi = _f(last["rsi_14"])

    score = 0

    if close > 0:
        distance = abs(close - ema7) / close * 100

        if distance <= 0.5:
            score += 30
        elif distance <= 1:
            score += 24
        elif distance <= 2:
            score += 16
        elif distance <= 3:
            score += 8

    if 42 <= rsi <= 55:
        score += 25
    elif 38 <= rsi < 42 or 55 < rsi <= 60:
        score += 15

    if 1.5 <= volume_ratio <= 3.5:
        score += 25
    elif volume_ratio >= 1.2:
        score += 12

    open_price = _f(last["open"])

    if open_price > 0:
        candle_change = (
            (close - open_price) / open_price * 100
        )

        if -0.5 <= candle_change <= 2.5:
            score += 20
        elif 2.5 < candle_change <= 4:
            score += 10

    return _clamp(score)


def _movement_score(
    data: pd.DataFrame,
    volume_ratio: float,
    volume_acceleration: bool,
) -> tuple[int, list[str]]:

    last = data.iloc[-1]
    prev = data.iloc[-2]

    close = _f(last["close"])
    ema7 = _f(last["ema_7"])
    ema21 = _f(last["ema_21"])
    macd = _f(last["macd"])
    macd_signal = _f(last["macd_signal"])
    hist = _f(last["macd_histogram"])
    prev_hist = _f(prev["macd_histogram"])
    vwap = _f(last["vwap"])

    score = 0
    reasons = []

    if close > ema7:
        score += 15
        reasons.append("Fiyat EMA7 üzerinde")

    if ema7 > ema21:
        score += 15
        reasons.append("EMA7 > EMA21")

    if macd > macd_signal:
        score += 15
        reasons.append("MACD pozitif")

    if hist > prev_hist:
        score += 10
        reasons.append("MACD histogram güçleniyor")

    if volume_ratio >= 2:
        score += 15
        reasons.append("Hacim güçlü")
    elif volume_ratio >= 1.5:
        score += 8

    if volume_acceleration:
        score += 10
        reasons.append("Hacim devam ediyor")

    if close >= vwap:
        score += 10
        reasons.append("VWAP geri alındı")

    if bool(last.get("volume_profile_support", False)):
        score += 5
        reasons.append("POC destek")

    cloud_top = max(
        _f(last.get("ichimoku_span_a")),
        _f(last.get("ichimoku_span_b")),
    )

    if close >= cloud_top:
        score += 5
        reasons.append("Bulut üstü")

    return _clamp(score), list(dict.fromkeys(reasons))


def _fake_risk(
    data: pd.DataFrame,
    volume_ratio: float,
) -> tuple[int, list[str]]:

    last = data.iloc[-1]
    prev = data.iloc[-2]

    risk = 0
    reasons = []

    hl, _ = _higher_low(data)

    if not bool(last.get("higher_low", False)) and not hl:
        risk += 20
        reasons.append("Higher-Low zayıf")

    if _f(last["rsi_14"]) < _f(prev["rsi_14"]):
        risk += 15
        reasons.append("RSI geri dönüyor")

    if volume_ratio < 1.2:
        risk += 20
        reasons.append("Hacim zayıf")

    if _f(last["close"]) < _f(last["ema_7"]):
        risk += 20
        reasons.append("Fiyat EMA7 altında")

    if _f(last["low"]) < _f(prev["low"]):
        risk += 15
        reasons.append("Son dip aşağı kırıldı")

    return _clamp(risk), reasons


def calculate_v29_signal(
    df: pd.DataFrame,
) -> dict[str, Any]:

    if len(df) < 60:
        return {
            "version": "V29",
            "signal": "WAIT",
            "status": "BEKLE",
            "score": 0,
            "reason": "En az 60 mum gerekli.",
        }

    data = add_indicators(df)
    data = _add_v29_indicators(data)

    volume = calculate_volume_metrics(data)

    volume_ratio = _f(
        volume["volume_ratio"]
    )

    volume_acceleration = detect_volume_acceleration(data)

    v28 = calculate_v28_signal(df)

    curve_score, curve_reasons = _curve_score(
        data,
        volume_ratio,
        volume_acceleration,
    )

    earlyness_score = _earlyness_score(
        data,
        volume_ratio,
    )

    movement_score, movement_reasons = _movement_score(
        data,
        volume_ratio,
        volume_acceleration,
    )

    absorption_score = _absorption_score(
        data,
        volume_ratio,
    )

    fake_risk, fake_reasons = _fake_risk(
        data,
        volume_ratio,
    )

    # V29 ağırlıkları:
    # Kıvrım ve erkenlik ana hedef.
    score = _clamp(
        curve_score * 0.45
        + earlyness_score * 0.25
        + movement_score * 0.20
        + absorption_score * 0.10
        - fake_risk * 0.10
    )

    td13 = bool(data.iloc[-1]["td13"])

    if td13:
        score = _clamp(score + 4)

    if score >= 78 and fake_risk <= 25:
        status = "KIVRIM ONAY"
        signal = "BUY"

    elif score >= 65 and fake_risk <= 45:
        status = "KIVRIM İZLE"
        signal = "WATCH"

    elif movement_score >= 72 and fake_risk <= 35:
        status = "İVME BAŞLADI"
        signal = "BUY"

    else:
        status = "BEKLE"
        signal = "WAIT"

    last = data.iloc[-1]

    return {
        "version": "V29",
        "signal": signal,
        "status": status,
        "score": score,
        "v28_score": int(v28.get("score", 0)),
        "curve_score": curve_score,
        "earlyness_score": earlyness_score,
        "movement_score": movement_score,
        "absorption_score": absorption_score,
        "fake_risk": fake_risk,
        "fake_risk_reasons": fake_reasons,
        "price": _f(last["close"]),
        "rsi": _f(last["rsi_14"]),
        "volume_ratio": volume_ratio,
        "volume_acceleration": volume_acceleration,
        "vwap": _f(last["vwap"]),
        "td13": td13,
        "td_buy_count": int(_f(last["td_buy"])),
        "curve_reasons": curve_reasons,
        "movement_reasons": movement_reasons,
        "reasons": list(
            dict.fromkeys(
                curve_reasons + movement_reasons
            )
        ),
    }
