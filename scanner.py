
import time

from market import (
    get_try_pairs,
    get_candles
)

from indicators import (
    prepare_dataframe,
    calculate_score
)

from app import send_telegram


sent = {}


def analyze_coin(symbol):

    candles = get_candles(
        symbol,
        interval="15m",
        limit=100
    )

    if len(candles) < 50:
        return


    df = prepare_dataframe(
        candles
    )


    result = calculate_score(
        df
    )


    score = result["score"]


    if score >= 75:

        price = df["close"].iloc[-1]


        message = f"""
🐋 BALİNA RADAR PRO

🪙 Coin: {symbol}

🎯 Güven:
{score}/100

💰 Fiyat:
{price}

📊 RSI:
{result['rsi']}

🔥 Hacim:
%{result['volume']}

⏱ Zaman:
15 Dakika
"""


        return message


    return None



def start_scanner():

    while True:

        print(
            "Tarama başladı..."
        )


        coins = get_try_pairs()


        for coin in coins:

            symbol = coin["symbol"]


            # Aynı coine 3 saat tekrar sinyal verme

            if symbol in sent:

                if time.time() - sent[symbol] < 10800:
                    continue



            signal = analyze_coin(
                symbol
            )


            if signal:

                send_telegram(
                    signal
                )

                sent[symbol] = time.time()


                time.sleep(2)



        print(
            "Tarama tamamlandı"
        )


        time.sleep(
            300
              )
