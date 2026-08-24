from __future__ import annotations

from typing import Any

import requests

from config import BINANCE_TR_BASE_URL


class BinanceTRClient:
    """
    Binance TR public market-data istemcisi.

    API key gerektirmez.
    Sadece herkese açık piyasa verilerini kullanır.
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

        # Binance TR sembollerinin tiplerini burada tutacağız.
        self.symbol_types: dict[str, int] = {}

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """
        GET isteği gönderir ve Binance TR cevabını kontrol eder.
        """

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
                message = (
                    data.get("msg")
                    or data.get("message")
                    or "Bilinmeyen API hatası"
                )

                raise RuntimeError(
                    f"Binance TR API hatası: "
                    f"{code} - {message}"
                )

            if "data" in data:
                return data["data"]

        return data

    def get_server_time(self) -> Any:
        """
        Binance TR sunucu zamanını getirir.
        """

        url = (
            f"{BINANCE_TR_BASE_URL}"
            "/open/v1/common/time"
        )

        return self._get(url)

    def get_symbols(
        self,
    ) -> list[dict[str, Any]]:
        """
        Binance TR'deki desteklenen işlem çiftlerini getirir.

        Sadece TRY işlem çiftlerini döndürür.
        """

        url = (
            f"{BINANCE_TR_BASE_URL}"
            "/open/v1/common/symbols"
        )

        data = self._get(url)

        if not isinstance(data, dict):
            raise RuntimeError(
                "Binance TR sembol cevabı beklenen formatta değil."
            )

        symbols = data.get("list", [])

        if not isinstance(symbols, list):
            raise RuntimeError(
                "Binance TR sembol listesi bulunamadı."
            )

        result: list[dict[str, Any]] = []

        for symbol in symbols:

            if not isinstance(symbol, dict):
                continue

            symbol_name = str(
                symbol.get("symbol", "")
            ).upper()

            quote_asset = str(
                symbol.get("quoteAsset", "")
            ).upper()

            if not symbol_name:
                continue

            if quote_asset != "TRY":
                continue

            # Binance TR dokümanındaki symbol type.
            symbol_type = int(
                symbol.get("type", 1)
            )

            self.symbol_types[
                symbol_name
            ] = symbol_type

            result.append(symbol)

        return result

    def get_symbol_type(
        self,
        symbol: str,
    ) -> int:
        """
        Bir sembolün Binance TR symbol type bilgisini döndürür.

        Eğer daha önce semboller alınmadıysa,
        sembol listesini otomatik olarak yeniler.
        """

        clean_symbol = symbol.upper()

        if clean_symbol not in self.symbol_types:
            self.get_symbols()

        return self.symbol_types.get(
            clean_symbol,
            1,
        )

    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
    ) -> list[list[Any]]:
        """
        Binance TR mum verilerini getirir.

        Symbol type 1:
            https://api.binance.me/api/v1/klines

        Symbol type 3:
            https://cloudme-tr.2meta.app/api/v1/klines
        """

        clean_symbol = (
            symbol
            .replace("_", "")
            .upper()
        )

        symbol_type = self.get_symbol_type(
            symbol
        )

        if symbol_type == 3:

            url = (
                "https://cloudme-tr.2meta.app"
                "/api/v1/klines"
            )

        else:

            url = (
                "https://api.binance.me"
                "/api/v1/klines"
            )

        params = {
            "symbol": clean_symbol,
            "interval": interval,
            "limit": min(
                max(int(limit), 1),
                1000,
            ),
        }

        data = self._get(
            url,
            params,
        )

        if not isinstance(data, list):
            raise RuntimeError(
                f"{symbol} için geçersiz "
                "mum verisi alındı."
            )

        return data

    def get_ticker_24h(
        self,
        symbol: str,
    ) -> dict[str, Any]:
        """
        24 saatlik ticker bilgisini getirir.

        Symbol type 1 için Binance uyumlu
        public endpoint kullanılır.
        """

        clean_symbol = (
            symbol
            .replace("_", "")
            .upper()
        )

        url = (
            "https://api.binance.me"
            "/api/v3/ticker/24hr"
        )

        data = self._get(
            url,
            {
                "symbol": clean_symbol,
            },
        )

        if not isinstance(data, dict):
            raise RuntimeError(
                f"{symbol} için geçersiz "
                "ticker verisi alındı."
            )

        return data
