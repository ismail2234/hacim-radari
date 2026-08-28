from __future__ import annotations

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


class TelegramNotifier:

    def __init__(
        self,
        bot_token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
    ) -> None:

        self.bot_token = bot_token
        self.chat_id = chat_id

    # ========================================================
    # AKTİF Mİ?
    # ========================================================

    @property
    def enabled(self) -> bool:
        return bool(
            self.bot_token
            and self.chat_id
        )

    # ========================================================
    # MESAJ GÖNDER
    # ========================================================

    def send_message(
        self,
        message: str,
    ) -> bool:

        if not self.enabled:
            return False

        url = (
            "https://api.telegram.org/"
            f"bot{self.bot_token}/sendMessage"
        )

        try:

            response = requests.post(
                url,
                data={
                    "chat_id": self.chat_id,
                    "text": message,
                },
                timeout=15,
            )

            response.raise_for_status()

            data = response.json()

            return bool(
                data.get("ok")
            )

        except Exception:
            return False

    # ========================================================
    # V30 SİNYAL MESAJI
    # ========================================================

    def send_signal(
        self,
        result: dict,
    ) -> bool:

        symbol = result.get(
            "symbol",
            "?",
        )

        price = float(
            result.get(
                "price",
                0,
            )
        )

        score = float(
            result.get(
                "score",
                0,
            )
        )

        status = result.get(
            "status",
            "UNKNOWN",
        )

        curve = float(
            result.get(
                "curve_score",
                0,
            )
        )

        volume = float(
            result.get(
                "volume_score",
                0,
            )
        )

        dip = float(
            result.get(
                "dip_score",
                0,
            )
        )

        momentum = float(
            result.get(
                "momentum_score",
                0,
            )
        )

        late_penalty = float(
            result.get(
                "late_penalty",
                0,
            )
        )

        fakeout_penalty = float(
            result.get(
                "fakeout_penalty",
                0,
            )
        )

        message = (
            "🐋 V30 ERKEN AL ADAYI\n\n"
            f"🪙 #{symbol}\n"
            f"💰 Fiyat: {price:.8f}\n\n"
            f"🎯 Skor: {score:.1f}/100\n"
            f"📌 Durum: {status}\n\n"
            f"〰️ Kıvrım: {curve:.1f}/25\n"
            f"📊 Hacim: {volume:.1f}/25\n"
            f"📍 Dip: {dip:.1f}/20\n"
            f"🚀 Momentum: {momentum:.1f}/15\n"
            f"⏰ Geç sinyal cezası: -{late_penalty:.1f}\n"
            f"⚠️ Fakeout cezası: -{fakeout_penalty:.1f}\n\n"
            "⚠️ Yatırım tavsiyesi değildir."
        )

        return self.send_message(
            message
      )
