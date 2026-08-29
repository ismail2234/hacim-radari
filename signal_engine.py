from __future__ import annotations

import pandas as pd

from config import (
    MIN_BUY_SCORE,
    MIN_VOLUME_TRY,
    MAX_LATE_MOVE_PERCENT,
    MAX_SINGLE_CANDLE_VOLUME_SPIKE,
)


def f(x, default=0.0):
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


class V30SignalEngine:

    def __init__(self, min_buy_score=MIN_BUY_SCORE):
        self.min_buy_score = float(min_buy_score)

    def analyze(self, symbol, df):

        if df is None or df.empty:
            return self.empty(symbol, "NO_DATA")

        if len(df) < 25:
            return self.empty(symbol, "INSUFFICIENT_DATA")

        last = df.iloc[-1]
        price = f(last.get("close"))
        volume = f(last.get("quote_volume"))

        if price <= 0:
            return self.empty(symbol, "INVALID_PRICE")

        if volume < MIN_VOLUME_TRY:
            return {
                "symbol": symbol,
                "signal": "IGNORE",
                "status": "LOW_VOLUME",
                "score": 0,
                "price": price,
                "volume_try": volume,
            }

        early = self.early(df)
        vol = self.volume(df)
        dip = self.dip(df)
        momentum = self.momentum(df)
        structure = self.structure(df)

        late = self.late_penalty(df)
        spike = self.spike_penalty(df)
        breakdown = self.breakdown(df)
        divergence = self.divergence(df)

        score = clamp(
            early
            + vol
            + dip
            + momentum
            + structure
            - late
            - spike
            - breakdown
            - divergence
        )

        if score >= self.min_buy_score:
            signal = "BUY"
        elif score >= 60:
            signal = "WATCH"
        else:
            signal = "IGNORE"

        if score >= 80:
            status = "EARLY_BUY"
        elif score >= 70:
            status = "STRONG_WATCH"
        elif score >= 60:
            status = "STRENGTHENING"
        else:
            status = "WATCH"

        return {
            "symbol": symbol,
            "signal": signal,
            "status": status,
            "score": round(score, 2),
            "price": price,
            "volume_try": round(volume, 2),
            "early_score": round(early, 2),
            "volume_score": round(vol, 2),
            "dip_score": round(dip, 2),
            "momentum_score": round(momentum, 2),
            "structure_score": round(structure, 2),
            "late_penalty": round(late, 2),
            "spike_penalty": round(spike, 2),
            "breakdown_penalty": round(breakdown, 2),
            "divergence_penalty": round(divergence, 2),
        }

    def early(self, df):
        c = pd.to_numeric(df["close"], errors="coerce")
        ch = c.pct_change().tail(7).dropna() * 100

        if len(ch) < 6:
            return 0

        old = ch.iloc[:3].mean()
        mid = ch.iloc[3:5].mean()
        new = ch.iloc[5:].mean()

        score = 0

        if old <= 0.2:
            score += 5
        if mid > old:
            score += 6
        if new > mid:
            score += 7
        if ch.iloc[-1] > 0:
            score += 4

        return clamp(score, 0, 25)

    def volume(self, df):
        v = pd.to_numeric(
            df["quote_volume"],
            errors="coerce",
        )

        cur = f(v.iloc[-1])
        ma5 = f(v.tail(5).mean())
        ma20 = f(v.tail(20).mean())

        if ma20 <= 0:
            return 0

        r5 = cur / ma5 if ma5 > 0 else 0
        r20 = cur / ma20

        score = 0

        if r5 >= 1.1:
            score += 5
        if r5 >= 1.3:
            score += 5
        if r5 >= 1.6:
            score += 3
        if r20 >= 1.1:
            score += 4
        if r20 >= 1.3:
            score += 4

        recent = v.tail(5).tolist()
        rising = sum(
            recent[i] > recent[i - 1]
            for i in range(1, len(recent))
        )

        if rising >= 2:
            score += 2
        if rising >= 3:
            score += 2

        return clamp(score, 0, 25)

    def dip(self, df):
        low = f(df["low"].tail(20).min())
        high = f(df["high"].tail(20).max())
        close = f(df["close"].iloc[-1])

        if high <= low:
            return 0

        pos = (close - low) / (high - low) * 100

        if pos <= 30:
            return 16
        if pos <= 40:
            return 14
        if pos <= 50:
            return 11
        if pos <= 60:
            return 6

        return 0

    def momentum(self, df):
        c = pd.to_numeric(df["close"], errors="coerce")

        now = f(c.iloc[-1])
        c3 = f(c.iloc[-4])
        c5 = f(c.iloc[-6])

        if c3 <= 0 or c5 <= 0:
            return 0

        r3 = (now / c3 - 1) * 100
        r5 = (now / c5 - 1) * 100

        score = 0

        if r3 > 0:
            score += 5
        if r3 >= 0.3:
            score += 3
        if r5 > 0:
            score += 3
        if 0 <= r5 <= 4:
            score += 4

        return clamp(score, 0, 15)

    def structure(self, df):
        x = df.tail(5)

        positive = int(
            (x["close"] > x["open"]).sum()
        )

        score = 0

        if positive >= 2:
            score += 4
        if positive >= 3:
            score += 3

        if f(x["low"].iloc[-1]) >= f(x["low"].iloc[-3]):
            score += 4

        if f(x["close"].iloc[-1]) > f(x["close"].iloc[-2]):
            score += 2

        if f(x["close"].iloc[-1]) > f(x["open"].iloc[-1]):
            score += 2

        return clamp(score, 0, 15)

    def late_penalty(self, df):
        c = pd.to_numeric(df["close"], errors="coerce")

        old = f(c.iloc[-6])
        now = f(c.iloc[-1])

        if old <= 0:
            return 0

        move = (now / old - 1) * 100

        if move <= MAX_LATE_MOVE_PERCENT:
            return 0

        return clamp(
            (move - MAX_LATE_MOVE_PERCENT) * 3.5,
            0,
            25,
        )

    def spike_penalty(self, df):
        v = pd.to_numeric(
            df["quote_volume"],
            errors="coerce",
        )

        prev = f(v.iloc[-2])
        cur = f(v.iloc[-1])

        if prev <= 0:
            return 0

        ratio = cur / prev

        if ratio < MAX_SINGLE_CANDLE_VOLUME_SPIKE:
            return 0

        return clamp(
            (ratio - MAX_SINGLE_CANDLE_VOLUME_SPIKE) * 2.5,
            0,
            15,
        )

    def breakdown(self, df):
        change = f(
            df.iloc[-1].get("price_change_1")
        )

        if change >= -1:
            return 0
        if change <= -4:
            return 15
        if change <= -2.5:
            return 10

        return 5

    def divergence(self, df):
        pc = f(
            df.iloc[-1].get("price_change_1")
        )
        vc = f(
            df.iloc[-1].get("volume_change_1")
        )

        penalty = 0

        if vc >= 100 and pc < -0.5:
            penalty += 8

        if vc >= 200 and abs(pc) < 0.2:
            penalty += 4

        return min(penalty, 12)

    @staticmethod
    def empty(symbol, status):
        return {
            "symbol": symbol,
            "signal": "IGNORE",
            "status": status,
            "score": 0,
            "price": 0,
            "volume_try": 0,
        }


signal_engine = V30SignalEngine()
