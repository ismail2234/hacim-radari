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
    # BINANCE TR MAIN + TRY PARİTELERİ
    # ========================================================

    def get_try_symbols(self) -> list[str]:

        symbols = self.client.get_symbols()

        result: list[str] = []

        for item in symbols:

            symbol = str(
                item.get(
                    "symbol",
                    "",
                )
            ).upper()

            if not symbol:
                continue

            # Binance TR market tipi
            #
            # 1 = MAIN
            # 2 = NEXT
            #
            try:
                symbol_type = int(
                    item.get(
                        "type",
                        0,
                    )
                )
            except (TypeError, ValueError):
                symbol_type = 0

            if symbol_type != 1:
                continue

            quote_asset = str(
                item.get(
                    "quoteAsset",
                    "",
                )
            ).upper()

            if quote_asset != "TRY":
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
    # TÜM BINANCE TR TRY PARİTELERİ
    # ========================================================

    def scan_all(
        self,
    ) -> list[dict[str, Any]]:

        results: list[dict[str, Any]] = []

        symbols = self.get_try_symbols()

        for symbol in symbols:

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
    # BUY ADAYLARI
    # ========================================================

    def buy_candidates(
        self,
    ) -> list[dict[str, Any]]:

        results = self.scan_all()

        candidates = [
            item
            for item in results
            if item.get(
                "signal"
            ) == "BUY"
        ]

        return sorted(
            candidates,
            key=lambda item: float(
                item.get(
                    "score",
                    0.0,
                )
            ),
            reverse=True,
        )


scanner = V30Scanner()
