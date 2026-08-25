from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd


@dataclass
class BacktestResult:
    total_signals: int
    successful_signals: int
    failed_signals: int
    success_rate: float
    average_lead_candles: float
    maximum_lead_candles: int


class V29BacktestEngine:
    """
    V29 erkenlik test motoru.

    Bir sinyal üretildiğinde sonraki N mum içinde
    belirlenen yükseliş gerçekleşti mi diye bakar.

    Bu bir performans garantisi değildir.
    Sadece algoritmanın geçmiş verideki davranışını
    ölçmek için kullanılır.
    """

    def __init__(
        self,
        lookahead_candles: int = 3,
        target_pct: float = 2.0,
    ) -> None:
        self.lookahead_candles = max(
            1,
            int(lookahead_candles),
        )

        self.target_pct = float(
            target_pct
        )

    def run(
        self,
        df: pd.DataFrame,
        signal_function: Callable[
            [pd.DataFrame],
            dict[str, Any]
        ],
    ) -> BacktestResult:

        if df.empty:
            return BacktestResult(
                0,
                0,
                0,
                0.0,
                0.0,
                0,
            )

        total = 0
        successful = 0
        failed = 0
        leads: list[int] = []

        for index in range(len(df)):
            history = df.iloc[
                : index + 1
            ].copy()

            result = signal_function(
                history
            )

            signal = str(
                result.get(
                    "signal",
                    "WAIT",
                )
            )

            if signal not in {
                "BUY",
                "WATCH",
            }:
                continue

            if index + 1 >= len(df):
                continue

            total += 1

            entry_price = float(
                df.iloc[index]["close"]
            )

            future_end = min(
                len(df),
                index
                + 1
                + self.lookahead_candles,
            )

            future = df.iloc[
                index + 1 : future_end
            ]

            target = entry_price * (
                1
                + self.target_pct / 100
            )

            hit = False
            lead = 0

            for offset, (_, candle) in enumerate(
                future.iterrows(),
                start=1,
            ):
                high = float(
                    candle["high"]
                )

                if high >= target:
                    hit = True
                    lead = offset
                    break

            if hit:
                successful += 1
                leads.append(lead)
            else:
                failed += 1

        success_rate = (
            successful / total * 100
            if total
            else 0.0
        )

        average_lead = (
            sum(leads) / len(leads)
            if leads
            else 0.0
        )

        maximum_lead = (
            max(leads)
            if leads
            else 0
        )

        return BacktestResult(
            total_signals=total,
            successful_signals=successful,
            failed_signals=failed,
            success_rate=round(
                success_rate,
                2,
            ),
            average_lead_candles=round(
                average_lead,
                2,
            ),
            maximum_lead_candles=maximum_lead,
          )
      
