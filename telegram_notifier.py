from __future__ import annotations

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


class TelegramNotifier:
    def __init__(self) -> None:
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID

    def is_configured(self) -> bool:
        return bool(
            self.bot_token
            and self.chat_id
        )

    def send_message(
        self,
        message: str,
    ) -> bool:

        if not self.is_configured():
            print(
                "[TELEGRAM] Token veya Chat ID "
                "tanımlı değil."
            )
            return False

        url = (
            "https://api.telegram.org/bot"
            f"{self.bot_token}"
            "/sendMessage"
        )

        payload = {
            "chat_id": self.chat_id,
            "text": message,
        }

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=10,
            )

            if response.status_code == 200:
                print(
                    "[TELEGRAM] Mesaj gönderildi."
                )
                return True

            print(
                "[TELEGRAM] Hata "
                f"{response.status_code}: "
                f"{response.text}"
            )

            return False

        except Exception as exc:
            print(
                "[TELEGRAM] Bağlantı hatası: "
                f"{exc}"
            )
            return False
