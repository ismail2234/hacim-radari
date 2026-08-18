"""
V26 - market_data.py
Borsadan mum (OHLCV) verisi ceker, coin listesi getirir.
"""

import pandas as pd

try:
    import ccxt
except ImportError:
    ccxt = None

from config import CONFIG


def _get_exchange():
    if ccxt is None:
        raise RuntimeError("ccxt kurulu degil. 'pip install ccxt' calistirin.")
    return getattr(ccxt, CONFIG["exchange"])()


def fetch_ohlcv(symbol: str, timeframe: str = None, limit: int = None) -> pd.DataFrame:
    """Belirtilen sembol icin mum verisi ceker."""
    ex = _get_exchange()
    timeframe = timeframe or CONFIG["timeframe"]
    limit = limit or CONFIG["lookback_candles"]

    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)
    return df


def get_tradable_symbols(quote: str = None) -> list:
    """Belirtilen paritedeki (orn. TRY) aktif coinleri listeler."""
    ex = _get_exchange()
    quote = quote or CONFIG["quote"]
    markets = ex.load_markets()
    return [s for s in markets if s.endswith(f"/{quote}") and markets[s].get("active")]


def get_24h_volume_try(symbol: str) -> float:
    """Sembolun 24 saatlik TRY hacmini dondurur (likidite filtresi icin)."""
    ex = _get_exchange()
    ticker = ex.fetch_ticker(symbol)
    return float(ticker.get("quoteVolume") or 0)
