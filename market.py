from __future__ import annotations

import time
from threading import Lock

from binance_client import BinanceClient
from config import Settings
from indicators import pct


class MarketData:
    """Eski koddaki DAILY_CACHE / MARKET_CACHE global dict + Lock ikilisinin
    sınıf içine alınmış hali. Davranış aynı, ama artık test için mock'lanabilir
    bir nesne (global state değil)."""

    def __init__(self, client: BinanceClient, cfg: Settings):
        self.client = client
        self.cfg = cfg
        self._daily_cache: dict = {}
        self._daily_lock = Lock()
        self._market_cache: dict = {}
        self._market_lock = Lock()

    def daily_trend(self, symbol: str) -> dict:
        now = time.time()
        with self._daily_lock:
            cached = self._daily_cache.get(symbol)
            if cached:
                ts, data = cached
                if now - ts < self.cfg.daily_cache_ttl:
                    return data

        data = self.client.klines(symbol, "1d", 100)

        if len(data) < 92:
            result = {"ok": False, "d30": 0, "d90": 0}
            with self._daily_lock:
                self._daily_cache[symbol] = (now, result)
            return result

        closed = data[:-1]
        try:
            closes = [float(x[4]) for x in closed]
        except (TypeError, ValueError):
            return {"ok": False, "d30": 0, "d90": 0}

        current = closes[-1]
        result = {
            "ok": True,
            "d30": pct(closes[-31], current),
            "d90": pct(closes[-91], current),
        }

        with self._daily_lock:
            self._daily_cache[symbol] = (now, result)
        return result

    def context(self) -> dict:
        now = time.time()
        with self._market_lock:
            cached = self._market_cache.get(self.cfg.market_symbol)
            if cached:
                ts, data = cached
                if now - ts < self.cfg.daily_cache_ttl:
                    return data

        data = self.client.klines(self.cfg.market_symbol, "5m", 20)

        if len(data) < 5:
            return {"ok": False, "momentum": 0, "state": "VERİ YOK"}

        try:
            closes = [float(x[4]) for x in data[:-1]]
        except (TypeError, ValueError):
            return {"ok": False, "momentum": 0, "state": "VERİ YOK"}

        if len(closes) < 4:
            return {"ok": False, "momentum": 0, "state": "VERİ YOK"}

        momentum = pct(closes[-4], closes[-1])

        if abs(momentum) >= self.cfg.market_move * 2:
            state = "AŞIRI HAREKETLİ"
        elif abs(momentum) >= self.cfg.market_move:
            state = "HAREKETLİ"
        elif momentum > 0.5:
            state = "POZİTİF"
        elif momentum < -0.5:
            state = "NEGATİF"
        else:
            state = "NÖTR"

        result = {"ok": True, "momentum": momentum, "state": state}
        with self._market_lock:
            self._market_cache[self.cfg.market_symbol] = (now, result)
        return result
        
