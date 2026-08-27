from __future__ import annotations

import json
import os
import time
from typing import Any


TRACK_FILE = "volume_watch.json"

MIN_RISING_CANDLES = 3

MAX_START_VOLUME = 5_000_000

LEVEL_MULTIPLIERS = [
    1.50,
    2.00,
    3.00,
    5.00,
    10.00,
]


class VolumeTracker:

    def __init__(
        self,
        track_file: str = TRACK_FILE,
    ) -> None:

        self.track_file = track_file
        self.data: dict[str, dict[str, Any]] = {}

        self._load()

    def _load(self) -> None:

        if not os.path.exists(
            self.track_file
        ):
            return

        try:

            with open(
                self.track_file,
                "r",
                encoding="utf-8",
            ) as file:

                data = json.load(file)

            if isinstance(data, dict):
                self.data = data

        except Exception:

            self.data = {}

    def _save(self) -> None:

        try:

            with open(
                self.track_file,
                "w",
                encoding="utf-8",
            ) as file:

                json.dump(
                    self.data,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )

        except Exception:
            pass

    @staticmethod
    def _symbol(
        symbol: str,
    ) -> str:

        return str(
            symbol
        ).upper().strip()

    @staticmethod
    def _format_volume(
        value: float,
    ) -> str:

        if value >= 1_000_000_000:
            return (
                f"{value / 1_000_000_000:.2f}B"
            )

        if value >= 1_000_000:
            return (
                f"{value / 1_000_000:.2f}M"
            )

        if value >= 1_000:
            return (
                f"{value / 1_000:.2f}K"
            )

        return f"{value:.0f}"

    def _next_level(
        self,
        start_volume: float,
        last_level: int,
        current_volume: float,
    ) -> tuple[int, float] | None:

        for index, multiplier in enumerate(
            LEVEL_MULTIPLIERS
        ):

            if index <= last_level:
                continue

            level = (
                start_volume * multiplier
            )

            if current_volume >= level:

                return index, level

        return None

    def update(
        self,
        symbol: str,
        volume: float,
        price: float = 0.0,
        price_change: float = 0.0,
    ) -> dict[str, Any]:

        symbol = self._symbol(symbol)

        try:
            volume = float(volume)
        except Exception:
            volume = 0.0

        try:
            price = float(price)
        except Exception:
            price = 0.0

        try:
            price_change = float(
                price_change
            )
        except Exception:
            price_change = 0.0

        if not symbol or volume <= 0:

            return {
                "symbol": symbol,
                "message": False,
            }

        now = time.time()

        if symbol not in self.data:

            self.data[symbol] = {
                "start_volume": volume,
                "last_volume": volume,
                "last_level": -1,
                "rising_count": 0,
                "price": price,
                "price_change": price_change,
                "created_at": now,
                "updated_at": now,
            }

            self._save()

            return {
                "symbol": symbol,
                "message": False,
                "current_volume": volume,
                "previous_volume": 0.0,
                "rising_count": 0,
            }

        item = self.data[symbol]

        start_volume = float(
            item.get(
                "start_volume",
                volume,
            )
        )

        previous_volume = float(
            item.get(
                "last_volume",
                0.0,
            )
        )

        last_level = int(
            item.get(
                "last_level",
                -1,
            )
        )

        if volume > previous_volume:

            rising_count = int(
                item.get(
                    "rising_count",
                    0,
                )
            ) + 1

        else:

            rising_count = 0

        if (
            rising_count
            >= MIN_RISING_CANDLES
        ):

            next_level = self._next_level(
                start_volume,
                last_level,
                volume,
            )

        else:

            next_level = None

        message = False
        level_value = 0.0
        level_index = last_level

        if next_level is not None:

            level_index, level_value = (
                next_level
            )

            message = True

        item["last_volume"] = volume
        item["last_level"] = level_index
        item["rising_count"] = rising_count
        item["price"] = price
        item["price_change"] = price_change
        item["updated_at"] = now

        self.data[symbol] = item

        self._save()

        return {
            "symbol": symbol,
            "message": message,
            "current_volume": volume,
            "previous_volume": previous_volume,
            "start_volume": start_volume,
            "rising_count": rising_count,
            "level": level_value,
            "price": price,
            "price_change": price_change,
        }

    def make_message(
        self,
        result: dict[str, Any],
    ) -> str:

        symbol = result["symbol"]

        current = float(
            result.get(
                "current_volume",
                0,
            )
        )

        previous = float(
            result.get(
                "previous_volume",
                0,
            )
        )

        start_volume = float(
            result.get(
                "start_volume",
                0,
            )
        )

        level = float(
            result.get(
                "level",
                0,
            )
        )

        price = float(
            result.get(
                "price",
                0,
            )
        )

        price_change = float(
            result.get(
                "price_change",
                0,
            )
        )

        rising_count = int(
            result.get(
                "rising_count",
                0,
            )
        )

        if previous > 0:

            volume_change = (
                (
                    current - previous
                )
                / previous
            ) * 100

        else:

            volume_change = 0.0

        return (
            "🐋 HACİM RADARI\n\n"
            f"🪙 #{symbol}\n"
            f"💰 Fiyat: {price:.8f}\n\n"
            f"📊 Hacim: "
            f"{self._format_volume(current)}\n"
            f"📈 Önceki: "
            f"{self._format_volume(previous)}\n"
            f"🚀 Değişim: "
            f"{volume_change:.1f}%\n"
            f"🔄 Art arda yükseliş: "
            f"{rising_count} mum\n\n"
            f"🎯 Başlangıç hacmi: "
            f"{self._format_volume(start_volume)}\n"
            f"🎯 Yeni basamak: "
            f"{self._format_volume(level)}\n"
            f"📈 Fiyat değişimi: "
            f"{price_change:.2f}%\n\n"
            "⚠️ Yatırım tavsiyesi değildir."
        )


volume_tracker = VolumeTracker()
