from __future__ import annotations

import time
from threading import Lock

from binance_client import BinanceClient
from config import Settings
from indicators import pct


class MarketData:
    def __init__(self, client: BinanceClient, cfg: Settings):
        self.client = client
        self.cfg = cfg

        self._daily_cache: dict[str, tuple[float, dict]] = {}
        self._market_cache: dict[str, tuple[float, dict]] = {}

        self._daily_lock = Lock()
        self._market_lock = Lock()

    def _get_daily_cached(self, symbol: str) -> dict | None:
        now = time.monotonic()

        with self._daily_lock:
            cached = self._daily_cache.get(symbol)

            if cached is None:
                return None

            timestamp, data = cached

            if now - timestamp < self.cfg.daily_cache_ttl:
                return data

            self._daily_cache.pop(symbol, None)

        return None

    def _set_daily_cache(self, symbol: str, data: dict) -> None:
        with self._daily_lock:
            self._daily_cache[symbol] = (
                time.monotonic(),
                data,
            )

    def _get_market_cached(self) -> dict | None:
        now = time.monotonic()
        symbol = self.cfg.market_symbol

        with self._market_lock:
            cached = self._market_cache.get(symbol)

            if cached is None:
                return None

            timestamp, data = cached

            if now - timestamp < self.cfg.daily_cache_ttl:
                return data

            self._market_cache.pop(symbol, None)

        return None

    def _set_market_cache(self, data: dict) -> None:
        with self._market_lock:
            self._market_cache[self.cfg.market_symbol] = (
                time.monotonic(),
                data,
            )

    def daily_trend(self, symbol: str) -> dict:
        cached = self._get_daily_cached(symbol)

        if cached is not None:
            return cached

        data = self.client.klines(
            symbol,
            "1d",
            100,
        )

        if len(data) < 92:
            result = {
                "ok": False,
                "d30": None,
                "d90": None,
            }
            self._set_daily_cache(symbol, result)
            return result

        closed = data[:-1]

        try:
            closes = [
                float(candle[4])
                for candle in closed
            ]
        except (TypeError, ValueError, IndexError):
            result = {
                "ok": False,
                "d30": None,
                "d90": None,
            }
            self._set_daily_cache(symbol, result)
            return result

        if len(closes) < 92:
            result = {
                "ok": False,
                "d30": None,
                "d90": None,
            }
            self._set_daily_cache(symbol, result)
            return result

        current = closes[-1]

        if current <= 0:
            result = {
                "ok": False,
                "d30": None,
                "d90": None,
            }
            self._set_daily_cache(symbol, result)
            return result

        result = {
            "ok": True,
            "d30": pct(closes[-31], current),
            "d90": pct(closes[-91], current),
        }

        self._set_daily_cache(symbol, result)

        return result

    def context(self) -> dict:
        cached = self._get_market_cached()

        if cached is not None:
            return cached

        symbol = self.cfg.market_symbol

        data = self.client.klines(
            symbol,
            "5m",
            20,
        )

        if len(data) < 5:
            return {
                "ok": False,
                "momentum": 0.0,
                "state": "VERİ YOK",
            }

        closed = data[:-1]

        try:
            closes = [
                float(candle[4])
                for candle in closed
            ]
        except (TypeError, ValueError, IndexError):
            return {
                "ok": False,
                "momentum": 0.0,
                "state": "VERİ YOK",
            }

        if len(closes) < 4:
            return {
                "ok": False,
                "momentum": 0.0,
                "state": "VERİ YOK",
            }

        momentum = pct(
            closes[-4],
            closes[-1],
        )

        move = abs(momentum)

        if move >= self.cfg.market_move * 2:
            state = "AŞIRI HAREKETLİ"
        elif move >= self.cfg.market_move:
            state = "HAREKETLİ"
        elif momentum > 0.5:
            state = "POZİTİF"
        elif momentum < -0.5:
            state = "NEGATİF"
        else:
            state = "NÖTR"

        result = {
            "ok": True,
            "momentum": momentum,
            "state": state,
        }

        self._set_market_cache(result)

        return result

    def clear_symbol(self, symbol: str) -> None:
        with self._daily_lock:
            self._daily_cache.pop(symbol, None)

    def clear_market(self) -> None:
        with self._market_lock:
            self._market_cache.pop(
                self.cfg.market_symbol,
                None,
            )

    def clear_all(self) -> None:
        with self._daily_lock:
            self._daily_cache.clear()

        with self._market_lock:
            self._market_cache.clear()
