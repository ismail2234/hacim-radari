from __future__ import annotations

import requests

from config import BINANCE_BASE_URL


class BinanceTRClient:

    def __init__(
        self,
        base_url: str = BINANCE_BASE_URL,
        timeout: int = 15,
    ) -> None:

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ========================================================
    # GENEL GET
    # ========================================================

    def _get(
        self,
        endpoint: str,
        params: dict | None = None,
    ):
        url = f"{self.base_url}{endpoint}"

        response = requests.get(
            url,
            params=params,
            timeout=self.timeout,
        )

        response.raise_for_status()

        return response.json()

    # ========================================================
    # EXCHANGE INFO
    # ========================================================

    def get_symbols(self) -> list[dict]:

        data = self._get(
            "/api/v3/exchangeInfo"
        )

        symbols = data.get(
            "symbols",
            [],
        )

        if not isinstance(
            symbols,
            list,
        ):
            return []

        return symbols

    # ========================================================
    # KLINES
    # ========================================================

    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
    ) -> list:

        return self._get(
            "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
        )

    # ========================================================
    # FİYAT
    # ========================================================

    def get_price(
        self,
        symbol: str,
    ) -> float:

        data = self._get(
            "/api/v3/ticker/price",
            params={
                "symbol": symbol,
            },
        )

        return float(
            data.get(
                "price",
                0,
            )
        )
