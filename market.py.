from __future__ import annotations

import time
from threading import Lock

from binance_client import BinanceClient
from config import Settings


def pct(old, new):
    if not old:
        return 0.0
    return (new - old) / old * 100


class MarketData:

    def __init__(self, client: BinanceClient, cfg: Settings):
        self.client = client
        self.cfg = cfg
        self.cache = {}
        self.lock = Lock()

    def daily_trend(self, symbol):
        key = ("daily", symbol)
        now = time.time()

        with self.lock:
            item = self.cache.get(key)
            if item and now - item[0] < 900:
                return item[1]

        data = self.client.klines(
            symbol,
            "1d",
            100
        )

        if len(data) < 92:
            return {
                "ok": False,
                "d30": 0,
                "d90": 0
            }

        try:
            closes = [
                float(x[4])
                for x in data[:-1]
            ]
        except Exception:
            return {
                "ok": False,
                "d30": 0,
                "d90": 0
            }

        current = closes[-1]

        result = {
            "ok": True,
            "d30": pct(closes[-31], current),
            "d90": pct(closes[-91], current)
        }

        with self.lock:
            self.cache[key] = (
                now,
                result
            )

        return result

    def context(self):
        symbol = self.cfg.market_symbol
        key = ("market", symbol)
        now = time.time()

        with self.lock:
            item = self.cache.get(key)
            if item and now - item[0] < 60:
                return item[1]

        data = self.client.klines(
            symbol,
            "5m",
            50
        )

        if len(data) < 10:
            return {
                "ok": False,
                "momentum": 0,
                "state": "VERİ YOK"
            }

        try:
            closes = [
                float(x[4])
                for x in data[:-1]
            ]
        except Exception:
            return {
                "ok": False,
                "momentum": 0,
                "state": "VERİ YOK"
            }

        momentum = pct(
            closes[-6],
            closes[-1]
        )

        if momentum <= -3:
            state = "BTC ZAYIF"
        elif momentum <= -1:
            state = "BTC NEGATİF"
        elif momentum >= 3:
            state = "BTC GÜÇLÜ"
        elif momentum >= 1:
            state = "BTC POZİTİF"
        else:
            state = "BTC NÖTR"

        result = {
            "ok": True,
            "momentum": momentum,
            "state": state
        }

        with self.lock:
            self.cache[key] = (
                now,
                result
            )

        return result
