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
        interval: str = "5m",
        candle_limit: int = 100,
    ) -> None:

        self.client = client or BinanceTRClient()

        self.interval = interval
        self.candle_limit = candle_limit

        self.telegram = TelegramNotifier()

    # ==========================================================
    # BINANCE TR TRY COINLERİ
    # ==========================================================

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

            if (
                symbol.endswith("_TRY")
                or symbol.endswith("TRY")
            ):

                result.append(symbol)

        return sorted(
            set(result)
        )

    # ==========================================================
    # TEK COIN
    # ==========================================================

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

        if df.empty:

            return {
                "symbol": symbol,
                "message": False,
            }

        last = df.iloc[-1]

        price = float(
            last["close"]
        )

        # ======================================================
        # EN ÖNEMLİ KISIM
        #
        # volume değil!
        #
        # quote_volume = TRY hacmi
        # ======================================================

        volume_try = float(
            last["quote_volume"]
        )

        price_change = 0.0

        if len(df) >= 2:

            previous_price = float(
                df.iloc[-2]["close"]
            )

            if previous_price > 0:

                price_change = (
                    (
                        price
                        - previous_price
                    )
                    / previous_price
                ) * 100

        result = volume_tracker.update(
            symbol=symbol,
            volume=volume_try,
            price=price,
            price_change=price_change,
        )

        return result

    # ==========================================================
    # TÜM BINANCE TR
    # ==========================================================

    def scan_all(
        self,
    ) -> list[dict[str, Any]]:

        results: list[
            dict[str, Any]
        ] = []

        for symbol in self.get_try_symbols():

            try:

                result = self.scan_symbol(
                    symbol
                )

                results.append(result)

                # SADECE GERÇEK HACİM KADEMESİ
                # GEÇİLDİĞİNDE TELEGRAM
                if result.get(
                    "message",
                    False,
                ):

                    message = (
                        volume_tracker.make_message(
                            result
                        )
                    )

                    self.telegram.send_message(
                        message
                    )

            except Exception as exc:

                results.append(
                    {
                        "symbol": symbol,
                        "message": False,
                        "error": str(exc),
                    }
                )

        return results


scanner = MarketScanner()
