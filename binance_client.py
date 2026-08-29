from __future__ import annotations

import requests

from config import (
    BINANCE_MARKET_BASE_URL,
    BINANCE_TR_BASE_URL,
)


class BinanceTRClient:

    def __init__(
        self,
        tr_base_url: str = BINANCE_TR_BASE_URL,
        market_base_url: str = BINANCE_MARKET_BASE_URL,
        timeout: int = 15,
    ) -> None:

        self.tr_base_url = tr_base_url.rstrip("/")
        self.market_base_url = market_base_url.rstrip("/")
        self.timeout = timeout

    # ========================================================
    # GENEL GET
    # ========================================================

    def _get(
        self,
        base_url: str,
        endpoint: str,
        params: dict | None = None,
    ):
        url = f"{base_url}{endpoint}"

        response = requests.get(
            url,
            params=params,
            timeout=self.timeout,
        )

        response.raise_for_status()

        data = response.json()

        # Binance TR genel API cevapları:
        #
        # {
        #     "code": 0,
        #     "msg": "Success",
        #     "data": ...
        # }
        #
        # Fakat market kline endpointi
        # doğrudan LISTE döndürebilir.
        #
        # Bu nedenle sadece dictionary ise
        # code kontrolü yapıyoruz.

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

    # ========================================================
    # BINANCE TR PARİTELERİ
    # ========================================================

    def get_symbols(
        self,
    ) -> list[dict]:

        data = self._get(
            self.tr_base_url,
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
        # Kline endpointi doğrudan liste döndürüyorsa
        # ----------------------------------------------------

        if isinstance(
            data,
            list,
        ):
            return data

        # ----------------------------------------------------
        # Bazı cevaplarda data alanının
        # içinde liste bulunabilir.
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
            self.tr_base_url,
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
