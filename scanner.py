from __future__ import annotations

from typing import Any

from binance_client import BinanceTRClient
from market_data import klines_to_dataframe
from v29_engine import calculate_v29_signal
from telegram_notifier import TelegramNotifier
from volume_tracker import volume_tracker


class MarketScanner:

    def __init__(
        self,
        client: BinanceTRClient | None = None,
        interval: str = "2h",
        candle_limit: int = 100,
    ) -> None:

        self.client = (
            client or BinanceTRClient()
        )

        self.interval = interval
        self.candle_limit = candle_limit
        self.telegram = TelegramNotifier()

    def get_try_symbols(
        self,
    ) -> list[str]:

        symbols = self.client.get_symbols()

        result: list[str] = []

        for item in symbols:

            symbol = str(
                item.get(
                    "symbol",
                    "",
                )
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

        df = klines_to_dataframe(
            klines
        )

        signal = calculate_v29_signal(
            symbol,
            df,
        )

        result = {
            "symbol": symbol,
            **signal,
        }

        volume = 0.0

        if (
            df is not None
            and not df.empty
            and "volume" in df.columns
        ):
            try:
                volume = float(
                    df["volume"].iloc[-1]
                )
            except Exception:
                volume = 0.0

        price = float(
            result.get(
                "price",
                0.0,
            ) or 0.0
        )

        price_change = float(
            result.get(
                "price_change",
                result.get(
                    "price_change_pct",
                    0.0,
                ),
            ) or 0.0
        )

        tracker_result = volume_tracker.update(
            symbol=symbol,
            volume=volume,
            price=price,
            price_change=price_change,
        )

        result["volume_tracker"] = tracker_result

        return result

    def _send_volume_message(
        self,
        result: dict[str, Any],
    ) -> None:

        tracker_result = result.get(
            "volume_tracker",
            {},
        )

        if not tracker_result.get(
            "message",
            False,
        ):
            return

        message = volume_tracker.make_message(
            tracker_result
        )

        self.telegram.send_message(
            message
        )

    def scan_all(
        self,
    ) -> list[dict[str, Any]]:

        results: list[dict[str, Any]] = []

        for symbol in self.get_try_symbols():

            try:

                result = self.scan_symbol(
                    symbol
                )

                results.append(result)

                self._send_volume_message(
                    result
                )

            except Exception as exc:

                results.append(
                    {
                        "symbol": symbol,
                        "signal": "ERROR",
                        "score": 0,
                        "version": "V29",
                        "error": str(exc),
                    }
                )

        return results

    def get_buy_signals(
        self,
    ) -> list[dict[str, Any]]:

        results = self.scan_all()

        return []


scanner = MarketScanner()
