from __future__ import annotations

from typing import Any

from binance_client import BinanceTRClient
from market_data import klines_to_dataframe
from v29_engine import calculate_v29_signal
from v29_alert_formatter import V29AlertFormatter
from telegram_notifier import TelegramNotifier


class MarketScanner:
    def __init__(
        self,
        client: BinanceTRClient | None = None,
        interval: str = "5m",
        candle_limit: int = 100,
    ) -> None:

        self.client = (
            client or BinanceTRClient()
        )

        self.interval = interval
        self.candle_limit = candle_limit

        # Telegram
        self.telegram = TelegramNotifier()

    # ========================================================
    # TRY SYMBOLS
    # ========================================================

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
                and symbol.endswith("_TRY")
            ):
                result.append(symbol)

        return sorted(
            set(result)
        )

    # ========================================================
    # SINGLE SYMBOL
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

        df = klines_to_dataframe(
            klines
        )

        signal = calculate_v29_signal(
    symbol,
    df
)

        result = {
            "symbol": symbol,
            **signal,
        }

        return result

    # ========================================================
    # SCAN ALL
    # ========================================================

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

                results.append(
                    result
                )

                # ------------------------------------------------
                # TELEGRAM
                # ------------------------------------------------
                #
                # Şimdilik sadece gerçek KIVRIM ONAY
                # sinyallerini Telegram'a gönderiyoruz.
                #
                # WATCH / WAIT gönderilmez.
                #

                if (
                    result.get(
                        "signal"
                    )
                    == "BUY"
                    and result.get(
                        "status"
                    )
                    == "KIVRIM ONAY"
                ):

                    message = (
                        V29AlertFormatter.format(
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
                        "signal": "ERROR",
                        "score": 0,
                        "version": "V29",
                        "error": str(exc),
                    }
                )

        return results

    # ========================================================
    # BUY SIGNALS
    # ========================================================

    def get_buy_signals(
        self,
    ) -> list[dict[str, Any]]:

        results = self.scan_all()

        return sorted(
            [
                x
                for x in results
                if (
                    x.get(
                        "signal"
                    )
                    == "BUY"
                )
            ],
            key=lambda x: float(
                x.get(
                    "score",
                    0,
                )
            ),
            reverse=True,
        )
