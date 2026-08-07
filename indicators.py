
import pandas as pd
import ta


def prepare_dataframe(candles):
    df = pd.DataFrame(
        candles,
        columns=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:
        df[col] = pd.to_numeric(df[col])

    return df


# RSI
def get_rsi(df):
    rsi = ta.momentum.RSIIndicator(
        df["close"],
        window=14
    )
    return round(float(rsi.rsi().iloc[-1]), 2)


# EMA
def get_ema(df):

    ema20 = ta.trend.EMAIndicator(
        df["close"],
        window=20
    ).ema_indicator().iloc[-1]

    ema50 = ta.trend.EMAIndicator(
        df["close"],
        window=50
    ).ema_indicator().iloc[-1]

    return float(ema20), float(ema50)


# MACD
def get_macd(df):

    macd = ta.trend.MACD(
        df["close"]
    )

    return (
        float(macd.macd().iloc[-1]),
        float(macd.macd_signal().iloc[-1])
    )


# Hacim gücü
def get_volume_power(df):

    current = df["volume"].iloc[-1]

    avg = df["volume"].rolling(
        20
    ).mean().iloc[-1]

    if avg == 0:
        return 0

    return round(
        (current / avg) * 100,
        2
    )


# Genel puan
def calculate_score(df):

    score = 0

    rsi = get_rsi(df)
    ema20, ema50 = get_ema(df)
    macd, signal = get_macd(df)
    volume = get_volume_power(df)


    if 40 < rsi < 65:
        score += 20

    if ema20 > ema50:
        score += 25

    if macd > signal:
        score += 25

    if volume > 150:
        score += 30


    return {
        "score": score,
        "rsi": rsi,
        "volume": volume
    }
