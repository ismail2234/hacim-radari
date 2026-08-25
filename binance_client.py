from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import BINANCE_TR_BASE_URL


class BinanceTRClient:
    """
    Binance TR public market-data istemcisi.

    API key gerektirmez.
    Sadece herkese açık piyasa verilerini kullanır.
    """

    def __init__(self, timeout: int = 8) -> None:
        self.timeout = timeout

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Hacim-Radari-V29)",
                "Accept": "application/json",
                "Connection": "keep-alive",
            }
        )

        retry = Retry(
            total=1,
            connect=1,
            read=1,
            status=1,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            respect_retry_after_header=True,
        )

        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=20,
            pool_maxsize=20,
        )

        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.symbol_types: dict[str, int] = {}

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> Any:

        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
            )

            print(
                f"[BINANCE] GET {response.status_code} "
                f"{response.url}"
            )

            if response.status_code == 403:
                raise RuntimeError(
                    f"BINANCE 403 | URL={response.url}"
                )

            if response.status_code == 429:
                retry_after = response.headers.get(
                    "Retry-After",
                    "5",
                )

                try:
                    wait_seconds = min(
                        int(retry_after),
                        15,
                    )
                except ValueError:
                    wait_seconds = 5

                print(
                    f"[BINANCE] 429 | "
                    f"bekleme={wait_seconds}s"
                )

                time.sleep(wait_seconds)

                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                )

            response.raise_for_status()

            try:
                data = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "Binance TR geçersiz JSON döndürdü."
                ) from exc

            if isinstance(data, dict):

                code = data.get("code")

                if code not in (
                    None,
                    0,
                    "0",
                ):
                    message = (
                        data.get("msg")
                        or data.get("message")
                        or "Bilinmeyen API hatası"
                    )

                    raise RuntimeError(
                        "Binance TR API hatası: "
                        f"{code} - {message}"
                    )

                if "data" in data:
                    return data["data"]

            return data

        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"BINANCE TIMEOUT | URL={url}"
            ) from exc

        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"BINANCE CONNECTION ERROR | URL={url}"
            ) from exc

        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                f"BINANCE HTTP ERROR | "
                f"URL={url} | {exc}"
            ) from exc
    # ========================================================
    # SERVER TIME
    # ========================================================

    def get_server_time(self) -> Any:
        url = (
            f"{BINANCE_TR_BASE_URL}"
            "/open/v1/common/time"
        )

        return self._get(url)

    # ========================================================
    # SYMBOLS
    # ========================================================

    def get_symbols(
        self,
    ) -> list[dict[str, Any]]:

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

            symbol_type_raw = symbol.get(
                "type",
                1,
            )

            try:
                symbol_type = int(
                    symbol_type_raw
                )
            except (
                TypeError,
                ValueError,
            ):
                symbol_type = 1

            self.symbol_types[
                symbol_name
            ] = symbol_type

            result.append(symbol)

        print(
            f"[BINANCE] TRY sembol sayısı: "
            f"{len(result)}"
        )

        return result

    # ========================================================
    # SYMBOL TYPE
    # ========================================================

    def get_symbol_type(
        self,
        symbol: str,
    ) -> int:

        clean_symbol = (
            symbol
            .replace("_", "")
            .upper()
        )

        if clean_symbol not in self.symbol_types:
            self.get_symbols()

        return self.symbol_types.get(
            clean_symbol,
            1,
        )

    # ========================================================
    # KLINES
    # ========================================================

    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
    ) -> list[list[Any]]:

        clean_symbol = (
            symbol
            .replace("_", "")
            .upper()
        )

        symbol_type = self.get_symbol_type(
            symbol
        )

        if symbol_type == 2:

            url = (
                f"{BINANCE_TR_BASE_URL}"
                "/open/v1/market/klines"
            )

        elif symbol_type == 3:

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
                max(
                    int(limit),
                    1,
                ),
                1000,
            ),
        }

        print(
            f"[BINANCE] KLINES "
            f"{clean_symbol} "
            f"type={symbol_type}"
        )

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

    # ========================================================
    # 24H TICKER
    # ========================================================

    def get_ticker_24h(
        self,
        symbol: str,
    ) -> dict[str, Any]:

        clean_symbol = (
            symbol
            .replace("_", "")
            .upper()
        )

        symbol_type = self.get_symbol_type(
            symbol
        )

        if symbol_type == 1:

            url = (
                "https://api.binance.me"
                "/api/v3/ticker/24hr"
            )

        elif symbol_type == 3:

            url = (
                "https://cloudme-tr.2meta.app"
                "/api/v1/ticker/24hr"
            )

        else:

            url = (
                f"{BINANCE_TR_BASE_URL}"
                "/open/v1/market/ticker/24hr"
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

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self) -> None:

        try:
            self.session.close()
        except Exception:
            pass
