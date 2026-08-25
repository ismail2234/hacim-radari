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
