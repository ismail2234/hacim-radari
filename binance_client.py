import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings

log = logging.getLogger("v28.binance")


class BinanceClient:

    def __init__(self, cfg: Settings):
        self.cfg = cfg

        self.session = requests.Session()

        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )

        adapter = HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=retry,
        )

        self.session.mount("https://", adapter)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 Balina-Radari-V28"
        })

        # Binance TR öncelikli.
        self.market_urls = [
            "https://www.binance.tr",
            "https://api.binance.me",
            str(cfg.base_url).rstrip("/"),
        ]

        # Aynı adres tekrar etmesin.
        self.market_urls = list(dict.fromkeys(self.market_urls))

    def _request(self, base_url, path, params=None):

        url = base_url.rstrip("/") + path

        try:
            r = self.session.get(
                url,
                params=params,
                timeout=15,
            )

            if r.status_code == 403:
                log.warning(
                    "Binance 403 | %s",
                    url,
                )
                return None

            r.raise_for_status()

            return r.json()

        except Exception as e:
            log.warning(
                "Binance API hatası | %s | %s",
                url,
                e,
            )
            return None

    def _market_request(self, path, params=None):

        for base_url in self.market_urls:

            data = self._request(
                base_url,
                path,
                params,
            )

            if data is not None:
                log.info(
                    "Binance bağlantısı OK | %s",
                    base_url,
                )
                return data

        log.error(
            "Tüm Binance bağlantıları başarısız."
        )

        return None

    def exchange_info(self):

        data = self._market_request(
            "/api/v3/exchangeInfo"
        )

        if isinstance(data, dict):
            return data

        return {}

    def tickers(self):

        data = self._market_request(
            "/api/v3/ticker/24hr"
        )

        if isinstance(data, list):
            return data

        # Bazı Binance TR cevapları data altında olabilir.
        if isinstance(data, dict):

            inner = data.get("data")

            if isinstance(inner, list):
                return inner

        return []

    def klines(
        self,
        symbol,
        interval="5m",
        limit=300,
    ):

        params = {
            "symbol": str(symbol).upper(),
            "interval": interval,
            "limit": min(int(limit), 1000),
        }

        # Binance TR dokümanında ana market kline
        # verisi api.binance.me üzerinden belirtiliyor.
        preferred = [
            "https://api.binance.me",
            "https://www.binance.tr",
            str(self.cfg.base_url).rstrip("/"),
        ]

        preferred = list(dict.fromkeys(preferred))

        for base_url in preferred:

            # api.binance.me için resmi endpoint
            if "api.binance.me" in base_url:
                path = "/api/v1/klines"
            else:
                path = "/api/v3/klines"

            data = self._request(
                base_url,
                path,
                params,
            )

            if isinstance(data, list):
                return data

            if isinstance(data, dict):

                inner = data.get("data")

                if isinstance(inner, list):
                    return inner

        log.warning(
            "%s klines alınamadı.",
            symbol,
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
