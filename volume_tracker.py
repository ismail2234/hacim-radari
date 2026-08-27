from __future__ import annotations

import json
import os
import time
from typing import Any


TRACK_FILE = "volume_watch.json"

LEVELS = [
    100_000,
    200_000,
    500_000,
    1_000_000,
    2_000_000,
    3_000_000,
    5_000_000,
    10_000_000,
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
            self.data = {}
            return

        try:

            with open(
                self.track_file,
                "r",
                encoding="utf-8",
            ) as file:

                loaded = json.load(file)

            if isinstance(
                loaded,
                dict,
            ):
                self.data = loaded
            else:
                self.data = {}

        except Exception:

            self.data = {}

    def _save(self) -> None:

        temporary = (
            self.track_file
            + ".tmp"
        )

        try:

            with open(
                temporary,
                "w",
                encoding="utf-8",
            ) as file:

                json.dump(
                    self.data,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )

            os.replace(
                temporary,
                self.track_file,
            )

        except Exception:

            try:
                if os.path.exists(
                    temporary
                ):
                    os.remove(
                        temporary
                    )
            except Exception:
                pass

    def _clean_symbol(
        self,
        symbol: str,
    ) -> str:

        return (
            str(symbol)
            .upper()
            .strip()
        )

    def _get_next_level(
        self,
        volume: float,
    ) -> int | None:

        for level in LEVELS:

            if volume < level:
                return level

        return None

    def _get_crossed_levels(
        self,
        previous_volume: float,
        current_volume: float,
    ) -> list[int]:

        crossed = []

        if current_volume <= previous_volume:
            return crossed

        for level in LEVELS:

            if (
                previous_volume
                < level
                <= current_volume
            ):
                crossed.append(
                    level
                )

        return crossed     def update(
        self,
        symbol: str,
        volume: float,
        price: float = 0.0,
        price_change: float = 0.0,
    ) -> dict[str, Any]:

        symbol = self._clean_symbol(
            symbol
        )

        try:
            volume = float(volume)
        except (
            TypeError,
            ValueError,
        ):
            volume = 0.0

        try:
            price = float(price)
        except (
            TypeError,
            ValueError,
        ):
            price = 0.0

        try:
            price_change = float(
                price_change
            )
        except (
            TypeError,
            ValueError,
        ):
            price_change = 0.0

        if not symbol or volume <= 0:

            return {
                "symbol": symbol,
                "tracking": False,
                "message": False,
                "levels": [],
            }

        now = time.time()

        current = self.data.get(
            symbol
        )

        if current is None:

            self.data[symbol] = {
                "start_volume": volume,
                "last_volume": volume,
                "last_price": price,
                "last_price_change": price_change,
                "last_level": 0,
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
            }

        previous_volume = float(
            current.get(
                "last_volume",
                0.0,
            )
        )

        crossed_levels = (
            self._get_crossed_levels(
                previous_volume,
                volume,
            )
        )

        last_level = int(
            current.get(
                "last_level",
                0,
            )
        )

        new_levels = [
            level
            for level in crossed_levels
            if level > last_level
        ]

        if new_levels:

            last_level = max(
                new_levels
            )

        current[
            "last_volume"
        ] = volume

        current[
            "last_price"
        ] = price

        current[
            "last_price_change"
        ] = price_change

        current[
            "last_level"
        ] = last_level

        current[
            "tracking"
        ] = True

        current[
            "updated_at"
        ] = now

        self.data[symbol] = current

        self._save()

        return {
            "symbol": symbol,
            "tracking": True,
            "message": bool(
                new_levels
            ),
            "levels": new_levels,
            "previous_volume": previous_volume,
            "current_volume": volume,
            "price": price,
            "price_change": price_change,
            "start_volume": float(
                current.get(
                    "start_volume",
                    volume,
                )
            ),
        }

    def remove(
        self,
        symbol: str,
    ) -> None:

        symbol = self._clean_symbol(
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

        symbol = self._clean_symbol(
            symbol
        )

        return self.data.get(
            symbol
        )

    def tracked_symbols(
        self,
    ) -> list[str]:

        return list(
            self.data.keys()
      )     @staticmethod
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

    @staticmethod
    def level_text(
        level: int,
    ) -> str:

        return VolumeTracker.format_volume(
            level
        )

    def make_message(
        self,
        result: dict[str, Any],
    ) -> str:

        symbol = result[
            "symbol"
        ]

        current_volume = float(
            result[
                "current_volume"
            ]
        )

        previous_volume = float(
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

        level_text = ", ".join(
            self.level_text(level)
            for level in levels
        )

        if not level_text:
            level_text = "TAKİP"

        if previous_volume > 0:

            change = (
                (
                    current_volume
                    - previous_volume
                )
                / previous_volume
            ) * 100

        else:

            change = 0.0

        return (
            "🐋 HACİM RADARI\n\n"
            f"🪙 #{symbol}\n"
            f"💰 Fiyat: {price:.8f}\n\n"
            f"📊 Hacim: "
            f"{self.format_volume(current_volume)}\n"
            f"📈 Önceki: "
            f"{self.format_volume(previous_volume)}\n"
            f"🚀 Artış: {change:.1f}%\n\n"
            f"🎯 Basamak: {level_text}\n"
            f"📈 Fiyat değişimi: "
            f"{price_change:.2f}%\n\n"
            "⚠️ Yatırım tavsiyesi değildir."
        )


volume_tracker = VolumeTracker()


def track_volume(
    symbol: str,
    volume: float,
    price: float = 0.0,
    price_change: float = 0.0,
) -> dict[str, Any]:

    return volume_tracker.update(
        symbol=symbol,
        volume=volume,
        price=price,
        price_change=price_change,
      )
