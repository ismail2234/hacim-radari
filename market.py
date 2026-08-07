
import requests
import time


BINANCE_URL = "https://api.binance.com"


def get_all_tickers():
    url = f"{BINANCE_URL}/api/v3/ticker/24hr"

    try:
        response = requests.get(
            url,
            timeout=10
        )

        return response.json()

    except Exception as e:
        print("Ticker hata:", e)
        return []


def get_try_pairs():

    tickers = get_all_tickers()

    coins = []

    for item in tickers:

        symbol = item.get("symbol")

        if symbol and symbol.endswith("TRY"):

            volume = float(
                item.get("quoteVolume", 0)
            )

            if volume > 100000:

                coins.append(
                    {
                        "symbol": symbol,
                        "volume": volume
                    }
                )

    return coins



def get_candles(symbol, interval="15m", limit=100):

    url = f"{BINANCE_URL}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }


    try:

        response = requests.get(
            url,
            params=params,
            timeout=10
        )

        candles = response.json()

        result = []

        for c in candles:

            result.append(
                [
                    c[0],
                    c[1],
                    c[2],
                    c[3],
                    c[4],
                    c[5]
                ]
            )

        return result


    except Exception as e:

        print(
            "Kline hata:",
            e
        )

        return []
