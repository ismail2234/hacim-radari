from __future__ import annotations

import json
import os
import time
from typing import Any


TRACK_FILE = "volume_watch.json"

# Aynı coin için kaç ardışık hacim artışı bekleyeceğiz?
MIN_RISING_CANDLES = 2

# Başlangıç hacmi çok büyükse takip etmiyoruz.
# Amacımız küçük hacimden başlayanları bulmak.
MAX_START_VOLUME = 500_000

# Başlangıç hacmine göre kademeler
LEVELS = [
    1.25,
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

    # ==========================================================
    # LOAD
    # ==========================================================

    def _load(self) -> None:

        if not os.path.exists(self.track_file):
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

    # ==========================================================
    # SAVE
    # ==========================================================

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

    # ==========================================================
    # FORMAT
    # ==========================================================

    @staticmethod
    def format_volume(
        value: float,
    ) -> str:

        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.2f}B"

        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f}M"

        if value >= 1_000:
            return f"{value / 1_000:.2f}K"

        return f"{value:.0f}"

    # ==========================================================
    # UPDATE
    # ==========================================================

    def update(
        self,
        symbol: str,
        volume: float,
        price: float = 0.0,
        price_change: float = 0.0,
    ) -> dict[str, Any]:

        symbol = str(symbol).upper().strip()

        try:
            volume = float(volume)
        except Exception:
            volume = 0.0

        try:
            price = float(price)
        except Exception:
            price = 0.0

        try:
            price_change = float(price_change)
        except Exception:
            price_change = 0.0

        if not symbol or volume <= 0:

            return {
                "symbol": symbol,
                "message": False,
            }

        now = time.time()

        # ======================================================
        # İLK KEZ GÖRÜYORSAK
        # ======================================================

        if symbol not in self.data:

            self.data[symbol] = {
                "start_volume": volume,
                "last_volume": volume,
                "rising_count": 0,
                "last_level": 0,
                "price": price,
                "price_change": price_change,
                "updated_at": now,
            }

            self._save()

            return {
                "symbol": symbol,
                "message": False,
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

        rising_count = int(
            item.get(
                "rising_count",
                0,
            )
        )

        last_level = int(
            item.get(
                "last_level",
                0,
            )
        )

        # ======================================================
        # HACİM YÜKSELİYOR MU?
        # ======================================================

        if volume > previous_volume:

            rising_count += 1

        else:

            # Hacim düşerse zinciri sıfırla.
            rising_count = 0

        # ======================================================
        # ÇOK YÜKSEK BAŞLANGIÇ HACMİNİ ELE
        # ======================================================

        low_volume_candidate = (
            start_volume <= MAX_START_VOLUME
        )

        message = False
        reached_level = 0.0

        # ======================================================
        # KADEMELİ HACİM YÜKSELİŞİ
        # ======================================================

        if (
            low_volume_candidate
            and rising_count >= MIN_RISING_CANDLES
        ):

            for index, multiplier in enumerate(LEVELS, start=1):

                target = start_volume * multiplier

                if (
                    volume >= target
                    and index > last_level
                ):

                    last_level = index
                    reached_level = target
                    message = True

                    break

        # ======================================================
        # KAYDET
        # ======================================================

        item["last_volume"] = volume
        item["rising_count"] = rising_count
        item["last_level"] = last_level
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
            "level": reached_level,
            "price": price,
            "price_change": price_change,
        }

    # ==========================================================
    # TELEGRAM MESAJI
    # ==========================================================

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

        start = float(
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

            change = (
                (current - previous)
                / previous
            ) * 100

        else:

            change = 0.0

        return (
            "🐋 HACİM RADARI\n\n"

            f"🪙 #{symbol}\n"

            f"💰 Fiyat: "
            f"{price:.8f}\n\n"

            f"📊 Hacim: "
            f"{self.format_volume(current)}\n"

            f"📈 Önceki: "
            f"{self.format_volume(previous)}\n"

            f"🚀 Değişim: "
            f"{change:.1f}%\n"

            f"🔄 Art arda yükseliş: "
            f"{rising_count} mum\n\n"

            f"🎯 Başlangıç: "
            f"{self.format_volume(start)}\n"

            f"🎯 Yeni hacim basamağı: "
            f"{self.format_volume(level)}\n"

            f"📈 Fiyat değişimi: "
            f"{price_change:.2f}%\n\n"

            "⚠️ Yatırım tavsiyesi değildir."
        )


volume_tracker = VolumeTracker()
