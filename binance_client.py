from __future__ import annotations

import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings
from rate_limiter import RateLimiter

log = logging.getLogger("v26.client")


def build_session():
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
        "User-Agent": "V26-Early-Breakout-Radar/1.0"
    })
    return session


class BinanceClient:

    WEIGHT = {
        "/api/v3/ticker/24hr": 40,
        "/api/v3/exchangeInfo": 10,
        "/api/v3/klines": 2,
    }

    def __init__(self, settings: Settings, limiter: RateLimiter):
        self.settings = settings
        self.limiter = limiter
        self.session = build_session()

    def _weight(self, path):
        return self.WEIGHT.get(path, 5)

    def api(self, path, params=None):
        self.limiter.acquire(self._weight(path))

        r = self.session.get(
            self.settings.base_url + path,
            params=params,
            timeout=self.settings.timeout,
        )

        r.raise_for_status()
        return r.json()

    def tickers(self):
        try:
            return self.api("/api/v3/ticker/24hr")
        except Exception as e:
            log.error("Ticker: %s", e)
            return []

    def exchange_info(self):
        try:
            return self.api("/api/v3/exchangeInfo")
        except Exception as e:
            log.error("ExchangeInfo: %s", e)
            return {}

    def klines(self, symbol, interval="5m", limit=150):
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
                "%s klines hatasi: %s",
                symbol,
                e,
            )
            return []

    def telegram(self, text, reply_to=None):
        if not self.settings.telegram_token:
            return None

        if not self.settings.telegram_chat:
            return None

        payload = {
            "chat_id": self.settings.telegram_chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        # Ayni coin icin onceki mesaja baglan
        if reply_to:
            payload["reply_parameters"] = {
                "message_id": int(reply_to)
            }

        try:
            r = self.session.post(
                f"https://api.telegram.org/bot"
                f"{self.settings.telegram_token}/sendMessage",
                json=payload,
                timeout=self.settings.timeout,
            )

            r.raise_for_status()
            data = r.json()

            if not data.get("ok"):
                return None

            return data["result"]["message_id"]

        except Exception as e:
            log.error("Telegram: %s", e)
            return None
