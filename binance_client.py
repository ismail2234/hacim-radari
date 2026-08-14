from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings
from rate_limiter import RateLimiter

log = logging.getLogger("balina.client")


def build_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )

    session = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=40,
        pool_maxsize=40,
        max_retries=retry,
    )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "User-Agent": "BalinaRadari-V24/1.0",
        "Accept": "application/json",
    })

    return session


class BinanceClient:
    WEIGHTS = {
        "/api/v3/ticker/24hr": 40,
        "/api/v3/exchangeInfo": 10,
        "/api/v3/klines": 2,
    }

    def __init__(
        self,
        settings: Settings,
        limiter: RateLimiter,
    ):
        self.settings = settings
        self.limiter = limiter
        self.session = build_session()

    def _weight_for(self, path: str) -> int:
        return self.WEIGHTS.get(path, 5)

    def _url(self, path: str) -> str:
        return self.settings.base_url.rstrip("/") + path

    def api(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        weight = self._weight_for(path)
        self.limiter.acquire(weight)

        response = self.session.get(
            self._url(path),
            params=params,
            timeout=self.settings.request_timeout,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "?")
            raise RuntimeError(
                f"Binance rate limit: HTTP 429, Retry-After={retry_after}"
            )

        response.raise_for_status()

        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Binance geçersiz JSON döndürdü: {path}"
            ) from exc

    def tickers(self) -> list:
        try:
            data = self.api("/api/v3/ticker/24hr")

            if not isinstance(data, list):
                log.error("Ticker beklenmeyen veri döndürdü")
                return []

            return data

        except Exception as exc:
            log.error("Ticker hatası: %s", exc)
            return []

    def exchange_info(self) -> dict:
        try:
            data = self.api("/api/v3/exchangeInfo")

            if not isinstance(data, dict):
                log.error("ExchangeInfo beklenmeyen veri döndürdü")
                return {}

            return data

        except Exception as exc:
            log.error("ExchangeInfo hatası: %s", exc)
            return {}

    def klines(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list:
        if not symbol or not interval:
            return []

        if limit <= 0:
            return []

        limit = min(limit, 1000)

        try:
            data = self.api(
                "/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "limit": limit,
                },
            )

            if not isinstance(data, list):
                log.error(
                    "%s %s: beklenmeyen kline verisi",
                    symbol,
                    interval,
                )
                return []

            return data

        except Exception as exc:
            log.debug(
                "%s %s klines hatası: %s",
                symbol,
                interval,
                exc,
            )
            return []

    def telegram(self, text: str) -> bool:
        token = self.settings.telegram_token
        chat = self.settings.telegram_chat

        if not token or not chat or not text:
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"

        try:
            response = self.session.post(
                url,
                json={
                    "chat_id": chat,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=self.settings.request_timeout,
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                return False

            return bool(data.get("ok"))

        except Exception as exc:
            log.error("Telegram hatası: %s", exc)
            return False

    def close(self) -> None:
        self.session.close()
