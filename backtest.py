from __future__ import annotations

from typing import Any

import pandas as pd

from market_data import prepare_market_data
from signal_engine import V30SignalEngine


class V30Backtester:
    """
    V30 geçmiş veri test motoru.

    Amaç:
    Bir sinyal oluştuğunda sonraki mumlarda fiyatın
    ne yaptığını ölçmek.
    """

    def __init__(
        self,
        engine: V30SignalEngine | None = None,
    ) -> None:

        self.engine = (
            engine
            or V30SignalEngine()
        )

    # ========================================================
    # TEK COIN BACKTEST
    # ========================================================

    def run(
        self,
        symbol: str,
        klines: list[list[Any]],
    ) -> dict[str, Any]:

        df = prepare_market_data(
            klines
        )

        if df.empty:
            return {
                "symbol": symbol,
                "error": "NO_DATA",
            }

        results: list[dict[str, Any]] = []

        # En az 25 mum geçmiş gerekiyor.
        start_index = 25

        # Sonraki 10 mumu ölçebilmek için
        # son 10 mumu sinyal üretiminde kullanmıyoruz.
        end_index = len(df) - 10

        if end_index <= start_index:
            return {
                "symbol": symbol,
                "error": "INSUFFICIENT_DATA",
            }

        for index in range(
            start_index,
            end_index,
        ):

            # Performance optimization: pass full DataFrame `df` and index `index`
            # to avoid creating redundant slice copies (`df.iloc[:index+1].copy()`)
            # for every candle step in the backtest loop.
            analysis = self.engine.analyze(
                symbol,
                df,
                idx=index,
            )

            if analysis.get(
                "signal"
            ) != "BUY":
                continue

            signal_price = float(
                df.iloc[index]["close"]
            )

            future = df.iloc[
                index + 1 :
                index + 11
            ]

            if future.empty:
                continue

            # ------------------------------------------------
            # SONRAKİ MUM GETİRİLERİ
            # ------------------------------------------------

            returns = {}

            for candle_count in (
                1,
                3,
                5,
                10,
            ):

                if len(future) >= candle_count:

                    future_price = float(
                        future.iloc[
                            candle_count - 1
                        ]["close"]
                    )

                    if signal_price > 0:

                        returns[
                            f"return_{candle_count}"
                        ] = (
                            (
                                future_price
                                - signal_price
                            )
                            / signal_price
                        ) * 100

            # ------------------------------------------------
            # MAKSİMUM YÜKSELİŞ
            # ------------------------------------------------

            highest_price = float(
                future["high"].max()
            )

            maximum_gain = (
                (
                    highest_price
                    - signal_price
                )
                / signal_price
            ) * 100

            # ------------------------------------------------
            # MAKSİMUM GERİ ÇEKİLME
            # ------------------------------------------------

            lowest_price = float(
                future["low"].min()
            )

            maximum_drawdown = (
                (
                    lowest_price
                    - signal_price
                )
                / signal_price
            ) * 100

            # ------------------------------------------------
            # SİNYAL KAYDI
            # ------------------------------------------------

            signal_result = {
                "symbol": symbol,
                "index": index,
                "time": str(
                    df.iloc[index][
                        "open_time"
                    ]
                ),
                "price": signal_price,
                "score": analysis.get(
                    "score",
                    0,
                ),
                "curve_score": analysis.get(
                    "curve_score",
                    0,
                ),
                "volume_score": analysis.get(
                    "volume_score",
                    0,
                ),
                "dip_score": analysis.get(
                    "dip_score",
                    0,
                ),
                "momentum_score": analysis.get(
                    "momentum_score",
                    0,
                ),
                "maximum_gain": maximum_gain,
                "maximum_drawdown": maximum_drawdown,
                **returns,
            }

            results.append(
                signal_result
            )

        return self._summarize(
            symbol,
            results,
        )

    # ========================================================
    # ÖZET
    # ========================================================

    def _summarize(
        self,
        symbol: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:

        if not results:

            return {
                "symbol": symbol,
                "signals": 0,
                "success_rate_3": 0.0,
                "success_rate_5": 0.0,
                "average_return_1": 0.0,
                "average_return_3": 0.0,
                "average_return_5": 0.0,
                "average_return_10": 0.0,
                "average_max_gain": 0.0,
                "average_max_drawdown": 0.0,
                "signals_detail": [],
            }

        frame = pd.DataFrame(
            results
        )

        def mean_column(
            name: str,
        ) -> float:

            if name not in frame.columns:
                return 0.0

            return float(
                frame[name]
                .dropna()
                .mean()
            )

        # ----------------------------------------------------
        # BAŞARI ORANLARI
        # ----------------------------------------------------

        success_rate_3 = 0.0

        if "return_3" in frame.columns:

            values = frame[
                "return_3"
            ].dropna()

            if len(values) > 0:

                success_rate_3 = (
                    float(
                        (
                            values > 0
                        ).sum()
                    )
                    / len(values)
                ) * 100

        success_rate_5 = 0.0

        if "return_5" in frame.columns:

            values = frame[
                "return_5"
            ].dropna()

            if len(values) > 0:

                success_rate_5 = (
                    float(
                        (
                            values > 0
                        ).sum()
                    )
                    / len(values)
                ) * 100

        return {
            "symbol": symbol,
            "signals": len(results),
            "success_rate_3": round(
                success_rate_3,
                2,
            ),
            "success_rate_5": round(
                success_rate_5,
                2,
            ),
            "average_return_1": round(
                mean_column(
                    "return_1"
                ),
                3,
            ),
            "average_return_3": round(
                mean_column(
                    "return_3"
                ),
                3,
            ),
            "average_return_5": round(
                mean_column(
                    "return_5"
                ),
                3,
            ),
            "average_return_10": round(
                mean_column(
                    "return_10"
                ),
                3,
            ),
            "average_max_gain": round(
                mean_column(
                    "maximum_gain"
                ),
                3,
            ),
            "average_max_drawdown": round(
                mean_column(
                    "maximum_drawdown"
                ),
                3,
            ),
            "signals_detail": results,
        }


def print_backtest_result(
    result: dict[str, Any],
) -> None:
    """
    Backtest sonucunu okunabilir şekilde yazdırır.
    """

    print()
    print("=" * 60)
    print(
        f"V30 BACKTEST | "
        f"{result.get('symbol', '?')}"
    )
    print("=" * 60)

    if result.get("error"):

        print(
            "HATA:",
            result["error"],
        )

        return

    print(
        "Sinyal sayısı:",
        result.get(
            "signals",
            0,
        ),
    )

    print(
        "3 mum pozitif:",
        f"{result.get('success_rate_3', 0):.2f}%",
    )

    print(
        "5 mum pozitif:",
        f"{result.get('success_rate_5', 0):.2f}%",
    )

    print(
        "Ortalama +1:",
        f"{result.get('average_return_1', 0):.3f}%",
    )

    print(
        "Ortalama +3:",
        f"{result.get('average_return_3', 0):.3f}%",
    )

    print(
        "Ortalama +5:",
        f"{result.get('average_return_5', 0):.3f}%",
    )

    print(
        "Ortalama +10:",
        f"{result.get('average_return_10', 0):.3f}%",
    )

    print(
        "Ortalama maksimum yükseliş:",
        f"{result.get('average_max_gain', 0):.3f}%",
    )

    print(
        "Ortalama maksimum düşüş:",
        f"{result.get('average_max_drawdown', 0):.3f}%",
    )

    print("=" * 60)
