from __future__ import annotations

import time
import requests

from config import (
    BINANCE_MARKET_BASE_URL,
)


class BinanceTRClient:

    def __init__(
        self,
        tr_base_url: str = "https://www.binance.tr",
        market_base_url: str = BINANCE_MARKET_BASE_URL,
        timeout: int = 15,
    ) -> None:

        # Sembol listesi HER ZAMAN Binance TR'den
        self.tr_base_url = "https://www.binance.tr"

        # MAIN kline için Binance TR dokümanındaki market endpoint
        self.market_base_url = market_base_url.rstrip("/")

        self.timeout = timeout

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 "
                "(Linux; Android 10) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120.0 Mobile Safari/537.36"
            ),
            "Accept": "application/json",
            "Connection": "keep-alive",
        })

    # ========================================================
    # GENEL GET
    # ========================================================

    def _get(
        self,
        base_url: str,
        endpoint: str,
        params: dict | None = None,
        retries: int = 3,
    ):

        url = f"{base_url}{endpoint}"

        last_error = None

        for attempt in range(retries):

            try:

                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                )

                response.raise_for_status()

                data = response.json()

                if isinstance(data, dict):

                    code = data.get("code")

                    if code not in (
                        None,
                        0,
                    ):

                        raise RuntimeError(
                            "Binance TR API hatası: "
                            f"code={code}, "
                            f"msg={data.get('msg')}"
                        )

                return data

            except Exception as exc:

                last_error = exc

                print(
                    f"[V31 API] Hata "
                    f"{attempt + 1}/{retries}: "
                    f"{url} | {exc}",
                    flush=True,
                )

                if attempt < retries - 1:
                    time.sleep(2)

        raise last_error

    # ========================================================
    # BINANCE TR PARİTELERİ
    # ========================================================

    def get_symbols(
        self,
    ) -> list[dict]:

        # ÖNEMLİ:
        # Bu endpoint kesinlikle api.binance.me'ye
        # gitmeyecek.
        data = self._get(
            "https://www.binance.tr",
            "/open/v1/common/symbols",
        )

        if not isinstance(
            data,
            dict,
        ):
            return []

        payload = data.get(
            "data",
            {},
        )

        if not isinstance(
            payload,
            dict,
        ):
            return []

        symbols = payload.get(
            "list",
            [],
        )

        if not isinstance(
            symbols,
            list,
        ):
            return []

        print(
            f"[V31 API] Binance TR sembol listesi: "
            f"{len(symbols)}",
            flush=True,
        )

        return symbols

    # ========================================================
    # KLINE / MUM VERİSİ
    # ========================================================

    def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 100,
    ) -> list:

        market_symbol = (
            str(symbol)
            .upper()
            .replace("_", "")
        )

        data = self._get(
            self.market_base_url,
            "/api/v1/klines",
            params={
                "symbol": market_symbol,
                "interval": interval,
                "limit": limit,
            },
        )

        # ----------------------------------------------------
        # Doğrudan liste
        # ----------------------------------------------------

        if isinstance(
            data,
            list,
        ):

            return data

        # ----------------------------------------------------
        # data içinde liste
        # ----------------------------------------------------

        if isinstance(
            data,
            dict,
        ):

            candles = data.get(
                "data",
                [],
            )

            if isinstance(
                candles,
                list,
            ):

                return candles

        return []

    # ========================================================
    # BINANCE TR SUNUCU ZAMANI
    # ========================================================

    def get_server_time(
        self,
    ) -> dict:

        data = self._get(
            "https://www.binance.tr",
            "/open/v1/common/time",
        )

        if isinstance(
            data,
            dict,
        ):

            return data

        return {
            "code": 0,
            "msg": "Success",
            "data": data,
        }
