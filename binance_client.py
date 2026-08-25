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

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

        self.session = requests.Session()

        # ----------------------------------------------------
        # HTTP HEADERS
        # ----------------------------------------------------

        self.session.headers.update(
            {
                "User-Agent": "Hacim-Radari/1.0",
                "Accept": "application/json",
                "Connection": "keep-alive",
            }
        )

        # ----------------------------------------------------
        # RETRY
        # ----------------------------------------------------

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(
                ["GET"]
            ),
            respect_retry_after_header=True,
        )

        adapter = HTTPAdapter(
            max_retries=retry
        )

        self.session.mount(
            "https://",
            adapter,
        )

        self.session.mount(
            "http://",
            adapter,
        )

        # ----------------------------------------------------
        # SYMBOL TYPES
        # ----------------------------------------------------

        self.symbol_types: dict[str, int] = {}

    # ========================================================
    # INTERNAL GET
    # ========================================================

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """
        Güvenli GET isteği.

        Bağlantı / timeout / JSON / HTTP hatalarını
        kontrollü şekilde yakalar.
        """

        last_error: Exception | None = None

        for attempt in range(1, 4):

            try:

                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                )

                # ------------------------------------------------
                # HTTP HATA KONTROLÜ
                # ------------------------------------------------

                if response.status_code == 429:

                    retry_after = response.headers.get(
                        "Retry-After",
                        "5",
                    )

                    try:
                        wait_seconds = min(
                            int(retry_after),
                            30,
                        )
                    except ValueError:
                        wait_seconds = 5

                    print(
                        f"[BINANCE] 429 rate limit | "
                        f"bekleme={wait_seconds}s"
                    )

                    time.sleep(
                        wait_seconds
                    )

                    continue

                if response.status_code >= 500:

                    print(
                        f"[BINANCE] Sunucu hatası "
                        f"{response.status_code} | "
                        f"deneme={attempt}/3"
                    )

                    time.sleep(
                        attempt
                    )

                    continue

                response.raise_for_status()

                # ------------------------------------------------
                # JSON
                # ------------------------------------------------

                try:
                    data = response.json()

                except ValueError as exc:

                    raise RuntimeError(
                        "Binance TR geçersiz JSON döndürdü."
                    ) from exc

                # ------------------------------------------------
                # BINANCE API RESPONSE
                # ------------------------------------------------

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

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.SSLError,
            ) as exc:

                last_error = exc

                print(
                    f"[BINANCE] Bağlantı hatası | "
                    f"deneme={attempt}/3 | "
                    f"{type(exc).__name__}"
                )

                if attempt < 3:

                    time.sleep(
                        attempt * 2
                    )

            except requests.exceptions.RequestException as exc:

                last_error = exc

                print(
                    f"[BINANCE] HTTP hatası | "
                    f"deneme={attempt}/3 | "
                    f"{exc}"
                )

                if attempt < 3:

                    time.sleep(
                        attempt * 2
                    )

            except Exception:

                # API formatı gibi gerçek programlama
                # hatalarını sessizce yutma.
                raise

        # --------------------------------------------------------
        # 3 DENEME DE BAŞARISIZ
        # --------------------------------------------------------

        raise RuntimeError(
            "Binance TR bağlantısı kurulamadı. "
            f"URL={url} | "
            f"son_hata={last_error}"
        )

    # ========================================================
    # SERVER TIME
    # ========================================================

    def get_server_time(self) -> Any:
        """
        Binance TR sunucu zamanını getirir.
        """

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
                "Binance TR sembol cevabı "
                "beklenen formatta değil."
            )

        symbols = data.get(
            "list",
            [],
        )

        if not isinstance(symbols, list):

            raise RuntimeError(
                "Binance TR sembol listesi "
                "bulunamadı."
            )

        result: list[dict[str, Any]] = []

        for symbol in symbols:

            if not isinstance(
                symbol,
                dict,
            ):
                continue

            symbol_name = str(
                symbol.get(
                    "symbol",
                    "",
                )
            ).upper()

            quote_asset = str(
                symbol.get(
                    "quoteAsset",
                    "",
                )
            ).upper()

            if not symbol_name:
                continue

            if quote_asset != "TRY":
                continue

            # Binance TR:
            # 1 = MAIN
            # 2 = NEXT
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

            result.append(
                symbol
            )

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
        """
        Sembolün Binance TR symbol type bilgisini döndürür.
        """

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
        """
        Binance TR mum verilerini getirir.
        """

        clean_symbol = (
            symbol
            .replace("_", "")
            .upper()
        )

        symbol_type = self.get_symbol_type(
            symbol
        )

        # ----------------------------------------------------
        # BINANCE TR MARKET DATA
        # ----------------------------------------------------

        if symbol_type == 2:

            # Güncel dokümantasyonda NEXT semboller için
            # ana endpoint yapısı değişebilir.
            # Öncelikle Binance TR public endpoint denenir.

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

        data = self._get(
            url,
            params,
        )

        if not isinstance(
            data,
            list,
        ):

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
        """
        24 saatlik ticker bilgisini getirir.
        """

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

        if not isinstance(
            data,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} için geçersiz "
                "ticker verisi alındı."
            )

        return data

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self) -> None:
        """
        HTTP session'ı kapatır.
        """

        try:
            self.session.close()

        except Exception:
            pass
