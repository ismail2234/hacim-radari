from __future__ import annotations

from typing import Any

import pandas as pd

from indicators import add_indicators
from signal_engine import calculate_signal as calculate_v28_signal
from volume_engine import (
    calculate_volume_metrics,
    detect_volume_acceleration,
)


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

    # --------------------------------------------------------
    # VWAP
    # --------------------------------------------------------

    typical = (
        data["high"]
        + data["low"]
        + data["close"]
    ) / 3.0

    vol = pd.to_numeric(
        data["volume"],
        errors="coerce",
    ).fillna(0)

    cumulative_vol = vol.cumsum()

    data["vwap"] = (
        (typical * vol).cumsum()
        / cumulative_vol.replace(
            0,
            float("nan"),
        )
    ).fillna(data["close"])

    # --------------------------------------------------------
    # TD BUY SETUP
    # --------------------------------------------------------

    buy = (
        data["close"]
        < data["close"].shift(4)
    )

    counts = []
    count = 0

    for ok in buy.fillna(False):
        count = count + 1 if ok else 0
        counts.append(count)

    data["td_buy"] = counts
    data["td13"] = data["td_buy"] >= 13

    return data


# ============================================================
# EMA7 KIVRIM KALİTESİ
# ============================================================

def _ema7_quality(
    data: pd.DataFrame,
) -> tuple[int, list[str]]:

    if len(data) < 8:
        return 0, []

    ema7 = data["ema_7"].astype(float)

    # Mevcut eğim
    slope_now = _f(
        ema7.iloc[-1]
        - ema7.iloc[-3]
    )

    # Önceki eğim
    slope_prev = _f(
        ema7.iloc[-3]
        - ema7.iloc[-5]
    )

    score = 0
    reasons: list[str] = []

    # --------------------------------------------------------
    # Pozitif eğim
    # --------------------------------------------------------

    if slope_now > 0:
        score += 12
        reasons.append(
            "EMA7 eğimi pozitif"
        )

    # --------------------------------------------------------
    # Eğim iyileşiyor
    # --------------------------------------------------------

    if slope_now > slope_prev:
        score += 12
        reasons.append(
            "EMA7 kıvrılıyor"
        )

    # --------------------------------------------------------
    # Negatiften pozitife geçiş
    # --------------------------------------------------------

    if (
        slope_prev <= 0
        and slope_now > 0
    ):
        score += 18
        reasons.append(
            "EMA7 negatiften pozitife dönüyor"
        )

    return _clamp(score), reasons


# ============================================================
# GERÇEK HIGHER-LOW
# ============================================================

def _higher_low(
    data: pd.DataFrame,
) -> tuple[bool, int]:

    if len(data) < 16:
        return False, 0

    # Son swing bölgesi
    recent_window = data[
        "low"
    ].iloc[-5:]

    # Önceki swing bölgesi
    previous_window = data[
        "low"
    ].iloc[-10:-5]

    recent_low = _f(
        recent_window.min()
    )

    previous_low = _f(
        previous_window.min()
    )

    if (
        recent_low <= 0
        or previous_low <= 0
    ):
        return False, 0

    delta = (
        (recent_low - previous_low)
        / previous_low
        * 100
    )

    # Çok küçük farkları
    # gerçek higher-low sayma.
    if delta < 0.15:
        return False, 0

    # Aşırı uzak bir sıçramayı
    # da kıvrım olarak kabul etme.
    if delta > 8:
        return False, 0

    quality = _clamp(
        delta * 25
    )

    return True, quality


# ============================================================
# SATIŞ REDDİ
# ============================================================

def _selling_rejection(
    data: pd.DataFrame,
) -> bool:

    if data.empty:
        return False

    last = data.iloc[-1]

    candle_range = _f(
        last["high"]
        - last["low"]
    )

    if candle_range <= 0:
        return False

    lower_wick = _f(
        min(
            last["open"],
            last["close"],
        )
        - last["low"]
    )

    close_position = _f(
        (
            last["close"]
            - last["low"]
        )
        / candle_range
    )

    return (
        lower_wick
        / candle_range
        >= 0.35
        and close_position >= 0.55
    )


# ============================================================
# ABSORPTION
# ============================================================

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


# ============================================================
# KIVRIM SKORU
# ============================================================

def _curve_score(
    data: pd.DataFrame,
    volume_ratio: float,
    volume_acceleration: bool,
) -> tuple[int, list[str]]:

    last = data.iloc[-1]
    prev = data.iloc[-2]

    score = 0
    reasons: list[str] = []

    # --------------------------------------------------------
    # Dip bölgesi
    # --------------------------------------------------------

    if bool(
        last.get(
            "near_dip",
            False,
        )
    ):
        score += 12
        reasons.append(
            "Dip bölgesi"
        )

    # --------------------------------------------------------
    # Higher-Low
    # --------------------------------------------------------

    hl, hl_quality = _higher_low(
        data
    )

    if (
        bool(
            last.get(
                "higher_low",
                False,
            )
        )
        or hl
    ):
        score += 18
        reasons.append(
            "Higher-Low"
        )

    # --------------------------------------------------------
    # EMA7 kıvrımı
    # --------------------------------------------------------

    ema_score, ema_reasons = (
        _ema7_quality(data)
    )

    score += ema_score
    reasons.extend(
        ema_reasons
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = _f(
        last["rsi_14"]
    )

    prev_rsi = _f(
        prev["rsi_14"]
    )

    if rsi > prev_rsi:
        score += 10
        reasons.append(
            "RSI yükseliyor"
        )

    if (
        prev_rsi < 45
        <= rsi
    ):
        score += 12
        reasons.append(
            "RSI dönüşü"
        )

    if 42 <= rsi <= 55:
        score += 8
        reasons.append(
            "RSI erken bölge"
        )

    # --------------------------------------------------------
    # Satış reddi
    # --------------------------------------------------------

    if _selling_rejection(data):
        score += 10
        reasons.append(
            "Dipte satış reddi"
        )

    # --------------------------------------------------------
    # Erken hacim
    # --------------------------------------------------------

    if (
        1.3
        <= volume_ratio
        <= 3.5
    ):
        score += 8
        reasons.append(
            "İlk hacim"
        )

    if volume_acceleration:
        score += 5
        reasons.append(
            "Hacim ivmesi"
        )

    return (
        _clamp(score),
        list(
            dict.fromkeys(
                reasons
            )
        ),
    )


# ============================================================
# ERKENLİK SKORU
# ============================================================

def _earlyness_score(
    data: pd.DataFrame,
    volume_ratio: float,
) -> int:

    last = data.iloc[-1]

    close = _f(
        last["close"]
    )

    ema7 = _f(
        last["ema_7"]
    )

    rsi = _f(
        last["rsi_14"]
    )

    score = 0

    # --------------------------------------------------------
    # Fiyat - EMA7 mesafesi
    # --------------------------------------------------------

    if close > 0:

        distance = (
            abs(
                close - ema7
            )
            / close
            * 100
        )

        if distance <= 0.5:
            score += 30

        elif distance <= 1:
            score += 24

        elif distance <= 2:
            score += 16

        elif distance <= 3:
            score += 8

    # --------------------------------------------------------
    # RSI erken bölge
    # --------------------------------------------------------

    if 42 <= rsi <= 55:
        score += 25

    elif (
        38 <= rsi < 42
        or 55 < rsi <= 60
    ):
        score += 15

    # --------------------------------------------------------
    # Hacim
    # --------------------------------------------------------

    if (
        1.3
        <= volume_ratio
        <= 3.5
    ):
        score += 25

    elif volume_ratio >= 1.2:
        score += 12

    # --------------------------------------------------------
    # Son mum büyüklüğü
    # --------------------------------------------------------

    open_price = _f(
        last["open"]
    )

    if open_price > 0:

        candle_change = (
            (
                close
                - open_price
            )
            / open_price
            * 100
        )

        if (
            -0.5
            <= candle_change
            <= 2.5
        ):
            score += 20

        elif (
            2.5
            < candle_change
            <= 4
        ):
            score += 10

    return _clamp(score)
# ============================================================
# HAREKET SKORU
# ============================================================

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
    reasons: list[str] = []

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

    if bool(
        last.get(
            "volume_profile_support",
            False,
        )
    ):
        score += 5
        reasons.append("POC destek")

    cloud_top = max(
        _f(
            last.get(
                "ichimoku_span_a"
            )
        ),
        _f(
            last.get(
                "ichimoku_span_b"
            )
        ),
    )

    if close >= cloud_top:
        score += 5
        reasons.append("Bulut üstü")

    return (
        _clamp(score),
        list(
            dict.fromkeys(
                reasons
            )
        ),
    )


# ============================================================
# FAKE RISK
# ============================================================

def _fake_risk(
    data: pd.DataFrame,
    volume_ratio: float,
) -> tuple[int, list[str]]:

    last = data.iloc[-1]
    prev = data.iloc[-2]

    risk = 0
    reasons: list[str] = []

    hl, _ = _higher_low(data)

    if (
        not bool(
            last.get(
                "higher_low",
                False,
            )
        )
        and not hl
    ):
        risk += 25
        reasons.append(
            "Higher-Low zayıf"
        )

    if _f(
        last["rsi_14"]
    ) < _f(
        prev["rsi_14"]
    ):
        risk += 15
        reasons.append(
            "RSI geri dönüyor"
        )

    if volume_ratio < 1.2:
        risk += 20
        reasons.append(
            "Hacim zayıf"
        )

    if _f(
        last["close"]
    ) < _f(
        last["ema_7"]
    ):
        risk += 20
        reasons.append(
            "Fiyat EMA7 altında"
        )

    if _f(
        last["low"]
    ) < _f(
        prev["low"]
    ):
        risk += 15
        reasons.append(
            "Son dip aşağı kırıldı"
        )

    return (
        _clamp(risk),
        reasons,
    )


# ============================================================
# ERKEN HAREKET KONTROLÜ
# ============================================================

def _is_too_late(
    data: pd.DataFrame,
    volume_ratio: float,
) -> tuple[bool, list[str]]:

    if len(data) < 6:
        return False, []

    last = data.iloc[-1]

    close = _f(
        last["close"]
    )

    open_price = _f(
        last["open"]
    )

    ema7 = _f(
        last["ema_7"]
    )

    rsi = _f(
        last["rsi_14"]
    )

    reasons: list[str] = []

    # --------------------------------------------------------
    # Son mum çok büyükse geç kalmış olabilir.
    # --------------------------------------------------------

    if open_price > 0:

        candle_change = (
            (
                close
                - open_price
            )
            / open_price
            * 100
        )

        if candle_change > 4:
            reasons.append(
                "Son mum fazla büyüdü"
            )

    # --------------------------------------------------------
    # Fiyat EMA7'den fazla uzaklaştıysa geç.
    # --------------------------------------------------------

    if close > 0:

        ema_distance = (
            abs(
                close - ema7
            )
            / close
            * 100
        )

        if ema_distance > 3:
            reasons.append(
                "Fiyat EMA7'den uzak"
            )

    # --------------------------------------------------------
    # RSI fazla yükseldiyse erken bölge bitmiş olabilir.
    # --------------------------------------------------------

    if rsi > 65:
        reasons.append(
            "RSI erken bölgeyi geçti"
        )

    # --------------------------------------------------------
    # Hacim aşırı patladıysa ilk kıvrım kaçmış olabilir.
    # --------------------------------------------------------

    if volume_ratio > 5:
        reasons.append(
            "Hacim patlamış"
        )

    return (
        len(reasons) > 0,
        reasons,
    )


# ============================================================
# GERÇEK KIVRIM ÇEKİRDEĞİ
# ============================================================

def _curve_core_valid(
    data: pd.DataFrame,
    curve_score: int,
    earlyness_score: int,
    fake_risk: int,
    volume_ratio: float,
) -> tuple[bool, list[str]]:

    last = data.iloc[-1]
    prev = data.iloc[-2]

    reasons: list[str] = []

    # --------------------------------------------------------
    # Higher-Low zorunlu
    # --------------------------------------------------------

    hl, _ = _higher_low(data)

    if not (
        hl
        or bool(
            last.get(
                "higher_low",
                False,
            )
        )
    ):
        reasons.append(
            "Gerçek Higher-Low yok"
        )

    # --------------------------------------------------------
    # RSI yükselmeli
    # --------------------------------------------------------

    rsi = _f(
        last["rsi_14"]
    )

    prev_rsi = _f(
        prev["rsi_14"]
    )

    if not (
        rsi > prev_rsi
        and 40 <= rsi <= 60
    ):
        reasons.append(
            "RSI erken dönüş şartı yok"
        )

    # --------------------------------------------------------
    # EMA7 kıvrımı
    # --------------------------------------------------------

    ema_score, _ = _ema7_quality(
        data
    )

    if ema_score < 12:
        reasons.append(
            "EMA7 kıvrımı zayıf"
        )

    # --------------------------------------------------------
    # Erkenlik
    # --------------------------------------------------------

    if earlyness_score < 55:
        reasons.append(
            "Erkenlik skoru düşük"
        )

    # --------------------------------------------------------
    # Kıvrım skoru
    # --------------------------------------------------------

    if curve_score < 48:
        reasons.append(
            "Kıvrım skoru düşük"
        )

    # --------------------------------------------------------
    # Fake risk
    # --------------------------------------------------------

    if fake_risk > 30:
        reasons.append(
            "Fake risk yüksek"
        )

    # --------------------------------------------------------
    # Hacim
    # --------------------------------------------------------

    if not (
        1.2 <= volume_ratio <= 4.5
    ):
        reasons.append(
            "Hacim kıvrım bölgesinde değil"
        )

    return (
        len(reasons) == 0,
        reasons,
    )


# ============================================================
# V29 ANA SİNYAL
# ============================================================

def calculate_v29_signal(
    df: pd.DataFrame,
) -> dict[str, Any]:

    if len(df) < 60:
        return {
            "version": "V29",
            "signal": "WAIT",
            "status": "BEKLE",
            "score": 0,
            "reason": (
                "En az 60 mum gerekli."
            ),
        }

    # --------------------------------------------------------
    # TEMEL İNDİKATÖRLER
    # --------------------------------------------------------

    data = add_indicators(df)

    data = _add_v29_indicators(
        data
    )

    # --------------------------------------------------------
    # HACİM
    # --------------------------------------------------------

    volume = calculate_volume_metrics(
        data
    )

    volume_ratio = _f(
        volume["volume_ratio"]
    )

    volume_acceleration = (
        detect_volume_acceleration(
            data
        )
    )

    # --------------------------------------------------------
    # V28
    # --------------------------------------------------------

    v28 = calculate_v28_signal(
        df
    )

    # --------------------------------------------------------
    # V29 SKORLARI
    # --------------------------------------------------------

    curve_score, curve_reasons = (
        _curve_score(
            data,
            volume_ratio,
            volume_acceleration,
        )
    )

    earlyness_score = (
        _earlyness_score(
            data,
            volume_ratio,
        )
    )

    movement_score, movement_reasons = (
        _movement_score(
            data,
            volume_ratio,
            volume_acceleration,
        )
    )

    absorption_score = (
        _absorption_score(
            data,
            volume_ratio,
        )
    )

    fake_risk, fake_reasons = (
        _fake_risk(
            data,
            volume_ratio,
        )
    )

    # --------------------------------------------------------
    # GENEL SKOR
    # --------------------------------------------------------

    score = _clamp(
        curve_score * 0.50
        + earlyness_score * 0.30
        + movement_score * 0.10
        + absorption_score * 0.10
        - fake_risk * 0.10
    )

    # --------------------------------------------------------
    # TD13 BONUS
    # --------------------------------------------------------

    td13 = bool(
        data.iloc[-1]["td13"]
    )

    if td13:
        score = _clamp(
            score + 3
        )

    # --------------------------------------------------------
    # GEÇ KALMIŞ MI?
    # --------------------------------------------------------

    too_late, late_reasons = (
        _is_too_late(
            data,
            volume_ratio,
        )
    )

    # --------------------------------------------------------
    # GERÇEK KIVRIM
    # --------------------------------------------------------

    curve_valid, curve_fail_reasons = (
        _curve_core_valid(
            data,
            curve_score,
            earlyness_score,
            fake_risk,
            volume_ratio,
        )
    )

    # --------------------------------------------------------
    # SİNYAL KARARI
    # --------------------------------------------------------

    if (
        curve_valid
        and not too_late
        and score >= 72
        and fake_risk <= 30
    ):

        signal = "BUY"
        status = "KIVRIM ONAY"

    elif (
        not too_late
        and curve_score >= 42
        and earlyness_score >= 45
        and fake_risk <= 40
    ):

        signal = "WATCH"
        status = "KIVRIM İZLE"

    else:

        signal = "WAIT"
        status = "BEKLE"

    # --------------------------------------------------------
    # GEÇ SİNYALİ ASLA BUY YAPMA
    # --------------------------------------------------------

    if too_late:
        signal = "WAIT"
        status = "GEÇ KALDI"

    # --------------------------------------------------------
    # SON MUM
    # --------------------------------------------------------

    last = data.iloc[-1]

    all_reasons = list(
        dict.fromkeys(
            curve_reasons
            + movement_reasons
        )
    )

    return {
        "version": "V29",
        "signal": signal,
        "status": status,
        "score": score,

        "v28_score": int(
            v28.get(
                "score",
                0,
            )
        ),

        "curve_score": curve_score,
        "earlyness_score": (
            earlyness_score
        ),
        "movement_score": (
            movement_score
        ),
        "absorption_score": (
            absorption_score
        ),

        "fake_risk": fake_risk,

        "fake_risk_reasons": (
            fake_reasons
        ),

        "price": _f(
            last["close"]
        ),

        "rsi": _f(
            last["rsi_14"]
        ),

        "volume_ratio": (
            volume_ratio
        ),

        "volume_acceleration": (
            volume_acceleration
        ),

        "vwap": _f(
            last["vwap"]
        ),

        "td13": td13,

        "td_buy_count": int(
            _f(
                last["td_buy"]
            )
        ),

        "curve_reasons": (
            curve_reasons
        ),

        "movement_reasons": (
            movement_reasons
        ),

        "late_reasons": (
            late_reasons
        ),

        "curve_fail_reasons": (
            curve_fail_reasons
        ),

        "reasons": all_reasons,
        }
