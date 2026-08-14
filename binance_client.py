from __future__ import annotations

import logging
import time
from threading import Lock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings
from rate_limiter import RateLimiter

log = logging.getLogger("balina.client")


def build_session() -> requests.Session:
    retry_args = dict(
        total=2, connect=2, read=2, backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    try:
        retry = Retry(allowed_methods=["GET", "POST"], **retry_args)
    except TypeError:
        retry = Retry(method_whitelist=["GET", "POST"], **retry_args)

    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=40, pool_maxsize=40, max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "BalinaRadari-V23/1.0"})
    return session


class BinanceClient:
    """
    Eski koddaki `api()` fonksiyonu doğrudan `S.request(...)` çağırıyordu --
    hiçbir ağırlık kontrolü yoktu. Burada her çağrı önce RateLimiter'dan
    bütçe talep ediyor; bütçe yoksa istek gönderilmeden bekliyor.

    Ağırlıklar Binance'in genel klines/ticker maliyetlerine yakın kaba
    tahminlerdir (spot API dokümantasyonundaki tipik değerler), tam
    hesap yerine güvenli bir üst sınır olarak kullanılır.
    """

    WEIGHT = {
        "/api/v3/ticker/24hr": 40,   # tüm semboller için tek çağrı, pahalı
        "/api/v3/exchangeInfo": 10,
        "/api/v3/klines": 2,
    }

    def __init__(self, settings: Settings, limiter: RateLimiter):
        self.settings = settings
        self.limiter = limiter
        self.session = build_session()

    def _weight_for(self, path: str) -> int:
        return self.WEIGHT.get(path, 5)

    def api(self, path: str, params: dict | None = None):
        self.limiter.acquire(self._weight_for(path))

        response = self.session.request(
            "GET",
            self.settings.base_url + path,
            params=params,
            timeout=self.settings.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    def tickers(self) -> list:
        try:
            return self.api("/api/v3/ticker/24hr")
        except Exception as e:
            log.error("Ticker: %s", e)
            return []

    def exchange_info(self) -> dict:
        try:
            return self.api("/api/v3/exchangeInfo")
        except Exception as e:
            log.error("ExchangeInfo: %s", e)
            return {}

    def klines(self, symbol: str, interval: str, limit: int) -> list:
        try:
            return self.api("/api/v3/klines", {
                "symbol": symbol, "interval": interval, "limit": limit,
            })
        except Exception as e:
            log.debug("%s %s: %s", symbol, interval, e)
            return []

    def telegram(self, text: str) -> bool:
        if not self.settings.telegram_token or not self.settings.telegram_chat:
            return False
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage",
                json={"chat_id": self.settings.telegram_chat, "text": text},
                timeout=self.settings.request_timeout,
            )
            response.raise_for_status()
            return bool(response.json().get("ok"))
        except Exception as e:
            log.error("Telegram: %s", e)
            return False
  
