class MarketData:

    def __init__(
        self,
        client,
        cfg,
    ):
        self.client = client
        self.cfg = cfg

    def klines(
        self,
        symbol,
        interval="5m",
        limit=300,
    ):
        return self.client.klines(
            symbol,
            interval,
            limit,
        )

    def ticker(
        self,
        symbol,
    ):
        data = self.client.tickers()

        for item in data:

            if str(
                item.get(
                    "symbol",
                    "",
                )
            ).upper() == str(
                symbol
            ).upper():

                return item

        return {}

    def price(
        self,
        symbol,
    ):
        ticker = self.ticker(
            symbol
        )

        try:
            return float(
                ticker.get(
                    "lastPrice",
                    0,
                )
            )
        except Exception:
            return 0.0

    def volume(
        self,
        symbol,
    ):
        ticker = self.ticker(
            symbol
        )

        try:
            return float(
                ticker.get(
                    "quoteVolume",
                    0,
                )
            )
        except Exception:
            return 0.0
