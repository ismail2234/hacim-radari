from __future__ import annotations

from typing import Any

from binance_client import BinanceTRClient
from market_data import klines_to_dataframe
from telegram_notifier import TelegramNotifier
from volume_tracker import volume_tracker


class MarketScanner:

    def __init__(
        self,
        client: BinanceTRClient | None = None,
        interval: str = "2h",
        candle_limit: int = 100,
    ) -> None:

        self.client = client or BinanceTRClient()
        self.interval = interval
        self.candle_limit = candle_limit
        self.telegram = TelegramNotifier()

    def get_try_symbols(self) -> list[str]:

        symbols = self.client.get_symbols()
        result: list[str] = []

        for item in symbols:

            symbol = str(
                item.get("symbol", "")
            ).upper()

            if (
                symbol
                and (
                    symbol.endswith("_TRY")
                    or symbol.endswith("TRY")
                )
            ):
                result.append(symbol)

        return sorted(set(result))

    def scan_symbol(
        self,
        symbol: str,
    ) -> dict[str, Any]:

        klines = self.client.get_klines(
            symbol=symbol,
            interval=self.interval,
            limit=self.candle_limit,
        )

        df = klines_to_dataframe(klines)

        if df.empty:
            return {
                "symbol": symbol,
                "volume_tracker": {
                    "message": False
                },
            }

        last = df.iloc[-1]

        price = float(last["close"])

        try:
            volume_try = float(
                last["quote_volume"]
            )
        except Exception:
            volume_try = 0.0

        price_change = 0.0

        if len(df) >= 2:

            previous_price = float(
                df.iloc[-2]["close"]
            )

            if previous_price > 0:
                price_change = (
                    (price - previous_price)
                    / previous_price
                ) * 100

        tracker_result = volume_tracker.update(
            symbol=symbol,
            volume=volume_try,
            price=price,
            price_change=price_change,
        )

        return {
            "symbol": symbol,
            "price": price,
            "volume_try": volume_try,
            "price_change": price_change,
            "volume_tracker": tracker_result,
        }

    def scan_all(
        self,
    ) -> list[dict[str, Any]]:

        results: list[dict[str, Any]] = []

        for symbol in self.get_try_symbols():

            try:

                result = self.scan_symbol(symbol)

                results.append(result)

                tracker_result = result.get(
                    "volume_tracker",
                    {},
                )

                if tracker_result.get(
                    "message",
                    False,
                ):

                    message = (
                        volume_tracker.make_message(
                            tracker_result
                        )
                    )

                    self.telegram.send_message(
                        message
                    )

            except Exception as exc:

                results.append(
                    {
                        "symbol": symbol,
                        "signal": "ERROR",
                        "error": str(exc),
                    }
                )

        return results

    def get_buy_signals(
        self,
    ) -> list[dict[str, Any]]:

        return []


scanner = MarketScanner()
