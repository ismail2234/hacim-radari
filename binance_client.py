import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings

log = logging.getLogger("v27.binance")


class BinanceClient:

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.session = requests.Session()

        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=retry,
        )

        self.session.mount(
            "https://",
            adapter,
        )

        self.session.headers.update({
            "User-Agent": "Balina-Radari-V27"
        })

    def get(self, path, params=None):

        url = self.cfg.base_url + path

        r = self.session.get(
            url,
            params=params,
            timeout=10,
        )

        r.raise_for_status()

        return r.json()

    def exchange_info(self):

        try:
            return self.get(
                "/api/v3/exchangeInfo"
            )
        except Exception as e:
            log.error(
                "exchangeInfo: %s",
                e,
            )
            return {}

    def tickers(self):

        try:
            return self.get(
                "/api/v3/ticker/24hr"
            )
        except Exception as e:
            log.error(
                "ticker: %s",
                e,
            )
            return []

    def klines(
        self,
        symbol,
        interval="5m",
        limit=300,
    ):

        try:
            return self.get(
                "/api/v3/klines",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "limit": limit,
                },
            )
        except Exception as e:
            log.warning(
                "%s klines: %s",
                symbol,
                e,
            )
            return []

    def telegram(self, text):

        token = self.cfg.telegram_token
        chat = self.cfg.telegram_chat

        if not token or not chat:
            return False

        url = (
            "https://api.telegram.org/bot"
            f"{token}/sendMessage"
        )

        payload = {
            "chat_id": chat,
            "text": text,
            "disable_web_page_preview": True,
        }

        try:

            r = self.session.post(
                url,
                json=payload,
                timeout=10,
            )

            r.raise_for_status()

            data = r.json()

            return bool(
                data.get("ok")
            )

        except Exception as e:

            log.error(
                "Telegram: %s",
                e,
            )

            return False
