from __future__ import annotations

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramNotifier:

    def __init__(self, bot_token=TELEGRAM_BOT_TOKEN, chat_id=TELEGRAM_CHAT_ID):
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self):
        return bool(self.bot_token and self.chat_id)

    def send_message(self, message):
        if not self.enabled:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

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
            return bool(response.json().get("ok"))

        except Exception as exc:
            print(f"[TELEGRAM] HATA: {exc}", flush=True)
            return False

    def send_signal(self, result):

        symbol = result.get("symbol", "?")
        price = float(result.get("price", 0))
        score = float(result.get("score", 0))
        status = result.get("status", "UNKNOWN")

        early = float(result.get("early_score", 0))
        volume = float(result.get("volume_score", 0))
        dip = float(result.get("dip_score", 0))
        momentum = float(result.get("momentum_score", 0))
        structure = float(result.get("structure_score", 0))

        late = float(result.get("late_penalty", 0))
        spike = float(result.get("spike_penalty", 0))
        breakdown = float(result.get("breakdown_penalty", 0))
        divergence = float(result.get("divergence_penalty", 0))

        message = (
            "🐋 V30 ERKEN AL ADAYI\n\n"
            f"🪙 #{symbol}\n"
            f"💰 Fiyat: {price:.8f}\n\n"
            f"🎯 Skor: {score:.1f}/100\n"
            f"📌 Durum: {status}\n\n"
            f"〰️ Erken kıvrım: {early:.1f}/25\n"
            f"📊 Hacim: {volume:.1f}/25\n"
            f"📍 Dip: {dip:.1f}/16\n"
            f"🚀 Momentum: {momentum:.1f}/15\n"
            f"🏗️ Yapı: {structure:.1f}/15\n"
            f"⏰ Geç hareket cezası: -{late:.1f}\n"
            f"⚠️ Hacim spike cezası: -{spike:.1f}\n"
            f"📉 Kırılım cezası: -{breakdown:.1f}\n"
            f"🔀 Diverjans cezası: -{divergence:.1f}\n\n"
            "⚠️ Yatırım tavsiyesi değildir."
        )

        return self.send_message(message)
