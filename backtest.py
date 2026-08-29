from __future__ import annotations

import pandas as pd

from market_data import prepare_market_data
from signal_engine import V30SignalEngine


class V30Backtester:

    def __init__(self):
        self.engine = V30SignalEngine()

    def run(self, symbol, klines):

        df = prepare_market_data(klines)

        if df.empty or len(df) < 40:
            return {
                "symbol": symbol,
                "signals": 0,
                "error": "Yeterli mum verisi yok.",
            }

        signals = []

        # Her geçmiş noktayı ayrı ayrı test et.
        for i in range(25, len(df) - 10):

            history = df.iloc[: i + 1].copy()

            result = self.engine.analyze(
                symbol,
                history,
            )

            if result.get("signal") != "BUY":
                continue

            price = float(
                df.iloc[i]["close"]
            )

            future = df.iloc[i + 1 : i + 11]

            if price <= 0 or future.empty:
                continue

            max_high = float(
                future["high"].max()
            )

            min_low = float(
                future["low"].min()
            )

            gain = (
                (max_high / price) - 1
            ) * 100

            drawdown = (
                (min_low / price) - 1
            ) * 100

            ret1 = self.future_return(
                df,
                i,
                1,
                price,
            )

            ret3 = self.future_return(
                df,
                i,
                3,
                price,
            )

            ret5 = self.future_return(
                df,
                i,
                5,
                price,
            )

            ret10 = self.future_return(
                df,
                i,
                10,
                price,
            )

            signals.append(
                {
                    "index": i,
                    "time": str(
                        df.iloc[i]["open_time"]
                    ),
                    "price": price,
                    "score": result.get(
                        "score",
                        0,
                    ),
                    "early_score": result.get(
                        "early_score",
                        0,
                    ),
                    "volume_score": result.get(
                        "volume_score",
                        0,
                    ),
                    "dip_score": result.get(
                        "dip_score",
                        0,
                    ),
                    "momentum_score": result.get(
                        "momentum_score",
                        0,
                    ),
                    "structure_score": result.get(
                        "structure_score",
                        0,
                    ),
                    "return_1": ret1,
                    "return_3": ret3,
                    "return_5": ret5,
                    "return_10": ret10,
                    "max_gain": gain,
                    "max_drawdown": drawdown,
                }
            )

        if not signals:
            return {
                "symbol": symbol,
                "signals": 0,
                "success_rate_3": 0,
                "success_rate_5": 0,
                "average_return_1": 0,
                "average_return_3": 0,
                "average_return_5": 0,
                "average_return_10": 0,
                "average_max_gain": 0,
                "average_max_drawdown": 0,
                "signal_details": [],
            }

        return {
            "symbol": symbol,
            "signals": len(signals),

            "success_rate_3": round(
                self.success_rate(
                    signals,
                    "return_3",
                ),
                2,
            ),

            "success_rate_5": round(
                self.success_rate(
                    signals,
                    "return_5",
                ),
                2,
            ),

            "average_return_1": round(
                self.average(
                    signals,
                    "return_1",
                ),
                3,
            ),

            "average_return_3": round(
                self.average(
                    signals,
                    "return_3",
                ),
                3,
            ),

            "average_return_5": round(
                self.average(
                    signals,
                    "return_5",
                ),
                3,
            ),

            "average_return_10": round(
                self.average(
                    signals,
                    "return_10",
                ),
                3,
            ),

            "average_max_gain": round(
                self.average(
                    signals,
                    "max_gain",
                ),
                3,
            ),

            "average_max_drawdown": round(
                self.average(
                    signals,
                    "max_drawdown",
                ),
                3,
            ),

            "signal_details": signals,
        }

    @staticmethod
    def future_return(
        df,
        index,
        candles,
        price,
    ):

        target = index + candles

        if target >= len(df):
            return 0

        future_price = float(
            df.iloc[target]["close"]
        )

        if price <= 0:
            return 0

        return round(
            ((future_price / price) - 1)
            * 100,
            3,
        )

    @staticmethod
    def average(
        signals,
        key,
    ):

        values = [
            float(x.get(key, 0))
            for x in signals
        ]

        if not values:
            return 0

        return sum(values) / len(values)

    @staticmethod
    def success_rate(
        signals,
        key,
    ):

        if not signals:
            return 0

        successful = sum(
            1
            for x in signals
            if float(x.get(key, 0)) > 0
        )

        return (
            successful
            / len(signals)
        ) * 100


backtester = V30Backtester()
