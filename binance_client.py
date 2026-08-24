from __future__ import annotations

from typing import Any

import requests

from config import BINANCE_TR_BASE_URL


class BinanceTRClient:
    """
    Binance TR public market-data istemcisi.

    Bu sınıf şimdilik sadece public endpoint'leri kullanır.
    API KEY veya SECRET KEY gerektirmez.
    """

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Hacim-Radari/1.0",
                "Accept": "application/json",
            }
        )

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = self.session.get(
            url,
            params=params,
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):
            code = data.get("code")

            if code not in (None, 0, "0"):
                message = data.get("msg") or data.get("message")
                raise RuntimeError(
                    f"Binance TR API hatası: {code} - {message}"
                )

            if "data" in data:
                return data["data"]

        return data

    def get_server_time(self) -> Any:
        """Binance TR sunucu zamanını döndürür."""
        url = f"{BINANCE_TR_BASE_URL}/open/v1/common/time"
        return self._get(url)

    def get_symbols(self) -> list[dict[str, Any]]:
        """
        Binance TR'de desteklenen işlem çiftlerini getirir.
        Sadece TRY çiftlerini filtreler.
        """
        url = f"{BINANCE_TR_BASE_URL}/open/v1/common/symbols"

        data = self._get(url)

        if isinstance(data, dict):
            symbols = data.get("list", [])
        elif isinstance(data, list):
            symbols = data
        else:
            symbols = []

        return [
            symbol
            for symbol in symbols
            if str(symbol.get("quoteAsset", "")).upper() == "TRY"
            and int(symbol.get("spotTradingEnable", 1)) == 1
        ]

    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
    ) -> list[list[Any]]:
        """
        Binance TR spot mum verilerini getirir.

        Örnek:
            symbol="BTC_TRY"
            interval="5m"
            limit=100
        """

        clean_symbol = symbol.replace("_", "").upper()

        url = "https://api.binance.me/api/v1/klines"

        params = {
            "symbol": clean_symbol,
            "interval": interval,
            "limit": min(max(limit, 1), 1000),
        }

        data = self._get(url, params)

        if not isinstance(data, list):
            raise RuntimeError(
                f"{symbol} için geçersiz kline verisi alındı."
            )

        return data

    def get_ticker_24h(self, symbol: str) -> dict[str, Any]:
        """
        24 saatlik ticker bilgisini getirir.
        """

        clean_symbol = symbol.replace("_", "").upper()

        url = "https://api.binance.me/api/v3/ticker/24hr"

        data = self._get(
            url,
            {"symbol": clean_symbol},
        )

        if not isinstance(data, dict):
            raise RuntimeError(
                f"{symbol} için geçersiz ticker verisi alındı."
            )

        return data
