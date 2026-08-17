from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings
from rate_limiter import RateLimiter


log = logging.getLogger("v26.client")


def build_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )

    session = requests.Session()

    adapter = HTTPAdapter(
        pool_connections=30,
        pool_maxsize=30,
        max_retries=retry,
    )

    session.mount("https://", adapter)

    session.headers.update({
        "User-Agent": "Balina-Radari-V26/1.0",
        "Accept": "application/json",
    })

    return session


class BinanceClient:

    WEIGHT = {
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

    def _weight(self, path: str) -> int:
        return self.WEIGHT.get(path, 5)

    def api(
        self,
        path: str,
        params: dict | None = None,
    ):
        self.limiter.acquire(
            self._weight(path)
        )

        url = (
            self.settings.base_url.rstrip("/")
            + path
        )

        response = self.session.get(
            url,
            params=params,
            timeout=self.settings.request_timeout,
        )

        response.raise_for_status()

        return response.json()

    # =========================================================
    # 24 SAAT VERİLERİ
    # =========================================================

    def tickers(self):
        try:
            return self.api(
                "/api/v3/ticker/24hr"
            )

        except Exception as e:
            log.error(
                "Ticker hatası: %s",
                e,
            )
            return []

    # =========================================================
    # MARKET BİLGİSİ
    # =========================================================

    def exchange_info(self):
        try:
            return self.api(
                "/api/v3/exchangeInfo"
            )

        except Exception as e:
            log.error(
                "ExchangeInfo hatası: %s",
                e,
            )
            return {}

    # =========================================================
    # KLINE
    # =========================================================

    def klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 150,
    ):
        try:
            return self.api(
                "/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "limit": limit,
                },
            )

        except Exception as e:
            log.warning(
                "%s klines hatası: %s",
                symbol,
                e,
            )
            return []

    # =========================================================
    # TELEGRAM
    # =========================================================

    def telegram(
        self,
        text: str,
        reply_to=None,
    ):
        token = (
            self.settings.telegram_token
        )

        chat = (
            self.settings.telegram_chat
        )

        if not token or not chat:
            log.warning(
                "Telegram ayarları eksik."
            )
            return None

        payload = {
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        if reply_to:
            payload[
                "reply_parameters"
            ] = {
                "message_id": int(
                    reply_to
                )
            }

        try:
            response = self.session.post(
                "https://api.telegram.org/"
                f"bot{token}/sendMessage",
                json=payload,
                timeout=self.settings.request_timeout,
            )

            response.raise_for_status()

            data = response.json()

            if not data.get("ok"):
                log.error(
                    "Telegram API hatası: %s",
                    data,
                )
                return None

            return data[
                "result"
            ]["message_id"]

        except Exception as e:
            log.error(
                "Telegram gönderim hatası: %s",
                e,
            )
            return None
