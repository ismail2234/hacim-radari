from __future__ import annotations

from typing import Any

from binance_client import BinanceTRClient
from market_data import klines_to_dataframe
from signal_engine import calculate_signal


class MarketScanner:
    def __init__(
        self,
        client: BinanceTRClient | None = None,
        interval: str = "5m",
        candle_limit: int = 100,
    ) -> None:
        self.client = client or BinanceTRClient()
        self.interval = interval
        self.candle_limit = candle_limit

    def get_try_symbols(self) -> list[str]:
        """
        Binance TR'deki aktif TRY işlem çiftlerini döndürür.
        """

        symbols = self.client.get_symbols()

        result: list[str] = []

        for item in symbols:
            symbol = item.get("symbol")

            if not symbol:
                continue

            symbol = str(symbol).upper()

            if symbol.endswith("_TRY"):
                result.append(symbol)

        return sorted(set(result))

    def scan_symbol(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        """
        Tek bir coin için mum verisini alır ve sinyal üretir.
        """

        klines = self.client.get_klines(
            symbol=symbol,
            interval=self.interval,
            limit=self.candle_limit,
        )

        df = klines_to_dataframe(klines)

        signal = calculate_signal(df)

        return {
            "symbol": symbol,
            **signal,
        }

    def scan_all(
        self,
    ) -> list[dict[str, Any]]:
        """
        Tüm TRY çiftlerini tarar.

        Bir coin hata verdiğinde tüm tarama durmaz.
        """

        symbols = self.get_try_symbols()

        results: list[dict[str, Any]] = []

        for symbol in symbols:
            try:
                result = self.scan_symbol(symbol)

                results.append(result)

            except Exception as exc:
                results.append(
                    {
                        "symbol": symbol,
                        "signal": "ERROR",
                        "score": 0,
                        "error": str(exc),
                    }
                )

        return results

    def get_buy_signals(
        self,
    ) -> list[dict[str, Any]]:
        """
        Sadece BUY sinyallerini döndürür.
        """

        results = self.scan_all()

        buy_signals = [
            result
            for result in results
            if result.get("signal") == "BUY"
        ]

        return sorted(
            buy_signals,
            key=lambda item: int(
                item.get("score", 0)
            ),
            reverse=True,
        )
