from __future__ import annotations

import json
import os
import time
from typing import Any


TRACK_FILE = "volume_watch.json"

LEVELS = [
    10_000,
    25_000,
    50_000,
    100_000,
    250_000,
    500_000,
    1_000_000,
    2_000_000,
    3_000_000,
    5_000_000,
    10_000_000,
    15_000_000,
    20_000_000,
    50_000_000,
    100_000_000,
    200_000_000,
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
            ) as f:

                data = json.load(f)

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
            ) as f:

                json.dump(
                    self.data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

        except Exception:
            pass

    def _symbol(
        self,
        symbol: str,
    ) -> str:

        return str(
            symbol
        ).upper().strip()

    def _crossed(
        self,
        previous: float,
        current: float,
    ) -> list[int]:

        if current <= previous:
            return []

        return [
            level
            for level in LEVELS
            if previous < level <= current
        ]

    def update(
        self,
        symbol: str,
        volume: float,
        price: float = 0.0,
        price_change: float = 0.0,
    ) -> dict[str, Any]:

        symbol = self._symbol(
            symbol
        )

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
                "tracking": False,
                "message": False,
                "levels": [],
            }

        now = time.time()

        if symbol not in self.data:

            self.data[symbol] = {
                "start_volume": volume,
                "last_volume": volume,
                "max_volume": volume,
                "last_price": price,
                "last_price_change": price_change,
                "last_level": 0,
                "rising_count": 0,
                "tracking": True,
                "created_at": now,
                "updated_at": now,
            }

            self._save()

            return {
                "symbol": symbol,
                "tracking": True,
                "message": False,
                "levels": [],
                "previous_volume": 0.0,
                "current_volume": volume,
                "price": price,
                "price_change": price_change,
            }

        item = self.data[symbol]

        previous = float(
            item.get(
                "last_volume",
                0.0,
            )
        )

        max_volume = float(
            item.get(
                "max_volume",
                previous,
            )
        )

        if volume > previous:
            rising_count = int(
                item.get(
                    "rising_count",
                    0,
                )
            ) + 1
        else:
            rising_count = 0

        crossed = self._crossed(
            previous,
            volume,
        )

        last_level = int(
            item.get(
                "last_level",
                0,
            )
        )

        new_levels = [
            level
            for level in crossed
            if level > last_level
        ]

        if new_levels:
            last_level = max(
                new_levels
            )

        if volume > max_volume:
            max_volume = volume

        item["last_volume"] = volume
        item["max_volume"] = max_volume
        item["last_price"] = price
        item["last_price_change"] = price_change
        item["last_level"] = last_level
        item["rising_count"] = rising_count
        item["tracking"] = True
        item["updated_at"] = now

        self.data[symbol] = item

        self._save()

        return {
            "symbol": symbol,
            "tracking": True,
            "message": bool(
                new_levels
            ),
            "levels": new_levels,
            "previous_volume": previous,
            "current_volume": volume,
            "max_volume": max_volume,
            "price": price,
            "price_change": price_change,
            "start_volume": float(
                item.get(
                    "start_volume",
                    volume,
                )
            ),
            "rising_count": rising_count,
        }

    @staticmethod
    def format_volume(
        value: float,
    ) -> str:

        value = float(value)

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

    def make_message(
        self,
        result: dict[str, Any],
    ) -> str:

        symbol = result[
            "symbol"
        ]

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

        levels = result.get(
            "levels",
            [],
        )

        rising_count = int(
            result.get(
                "rising_count",
                0,
            )
        )

        if previous > 0:

            change = (
                (
                    current
                    - previous
                )
                / previous
            ) * 100

        else:

            change = 0.0

        level_text = ", ".join(
            self.format_volume(level)
            for level in levels
        )

        return (
            "🐋 HACİM RADARI\n\n"
            f"🪙 #{symbol}\n"
            f"💰 Fiyat: {price:.8f}\n\n"
            f"📊 Hacim: "
            f"{self.format_volume(current)}\n"
            f"📈 Önceki: "
            f"{self.format_volume(previous)}\n"
            f"🚀 Değişim: {change:.1f}%\n"
            f"🔄 Art arda yükseliş: "
            f"{rising_count} mum\n\n"
            f"🎯 Hacim basamağı: "
            f"{level_text}\n"
            f"📈 Fiyat değişimi: "
            f"{price_change:.2f}%\n\n"
            "⚠️ Yatırım tavsiyesi değildir."
        )

    def remove(
        self,
        symbol: str,
    ) -> None:

        symbol = self._symbol(
            symbol
        )

        if symbol in self.data:

            del self.data[symbol]
            self._save()

    def clear(self) -> None:

        self.data = {}
        self._save()

    def get(
        self,
        symbol: str,
    ) -> dict[str, Any] | None:

        return self.data.get(
            self._symbol(symbol)
        )

    def tracked_symbols(
        self,
    ) -> list[str]:

        return list(
            self.data.keys()
        )


volume_tracker = VolumeTracker()
