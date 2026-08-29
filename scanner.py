from __future__ import annotations

from typing import Any

from binance_client import BinanceTRClient
from market_data import prepare_market_data
from signal_engine import V30SignalEngine


class V30Scanner:

    def __init__(
        self,
        client: BinanceTRClient | None = None,
        interval: str = "5m",
        candle_limit: int = 100,
    ) -> None:

        self.client = client or BinanceTRClient()

        self.interval = interval
        self.candle_limit = candle_limit

        self.engine = V30SignalEngine()

    # ========================================================
    # TRY PARİTELERİ
    # ========================================================

    def get_try_symbols(self) -> list[str]:

        symbols = self.client.get_symbols()

        result = []

        for item in symbols:

            symbol = str(
                item.get(
                    "symbol",
                    "",
                )
            ).upper()

            status = str(
                item.get(
                    "status",
                    "",
                )
            ).upper()

            if status and status != "TRADING":
                continue

            if (
                symbol.endswith("_TRY")
                or symbol.endswith("TRY")
            ):
                result.append(symbol)

        return sorted(
            set(result)
        )

    # ========================================================
    # TEK COIN
    # ========================================================

    def scan_symbol(
        self,
        symbol: str,
    ) -> dict[str, Any]:

        klines = self.client.get_klines(
            symbol=symbol,
            interval=self.interval,
            limit=self.candle_limit,
        )

        df = prepare_market_data(
            klines
        )

        if df.empty:

            return {
                "symbol": symbol,
                "signal": "IGNORE",
                "status": "NO_DATA",
                "score": 0.0,
            }

        return self.engine.analyze(
            symbol,
            df,
        )

    # ========================================================
    # TÜM PARİTELER
    # ========================================================

    def scan_all(
        self,
    ) -> list[dict[str, Any]]:

        results = []

        for symbol in self.get_try_symbols():

            try:

                result = self.scan_symbol(
                    symbol
                )

                results.append(
                    result
                )

            except Exception as exc:

                results.append(
                    {
                        "symbol": symbol,
                        "signal": "ERROR",
                        "status": "ERROR",
                        "score": 0.0,
                        "error": str(exc),
                    }
                )

        return results

    # ========================================================
    # SADECE AL ADAYLARI
    # ========================================================

    def buy_candidates(
        self,
    ) -> list[dict[str, Any]]:

        results = self.scan_all()

        return sorted(
            [
                item
                for item in results
                if item.get(
                    "signal"
                ) == "BUY"
            ],
            key=lambda item: float(
                item.get(
                    "score",
                    0,
                )
            ),
            reverse=True,
        )


scanner = V30Scanner()
