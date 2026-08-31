from __future__ import annotations

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramNotifier:
    def __init__(
        self,
        bot_token=TELEGRAM_BOT_TOKEN,
        chat_id=TELEGRAM_CHAT_ID,
    ):
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

        message = (
            "🐋 V33 ERKEN KIVRIM\n\n"
            f"🪙 #{symbol}\n"
            f"💰 Fiyat: {price:.8f}\n"
            f"🎯 Skor: {score:.1f}/100\n\n"
            f"〰️ Erken kıvrım: {float(result.get('early_score', 0)):.1f}/25\n"
            f"📊 Hacim: {float(result.get('volume_score', 0)):.1f}/25\n"
            f"📍 Dip: {float(result.get('dip_score', 0)):.1f}/16\n"
            f"🚀 Momentum: {float(result.get('momentum_score', 0)):.1f}/15\n"
            f"🏗️ Yapı: {float(result.get('structure_score', 0)):.1f}/15\n\n"
            f"📈 TRY hacmi: {float(result.get('try_volume', 0)):.0f}\n"
            f"🔄 Hacim oranı: {float(result.get('volume_ratio', 0)):.2f}x\n"
            f"📈 Son 3 mum: {float(result.get('ret3', 0)):+.2f}%\n"
            f"📉 Son 6 mum: {float(result.get('ret6', 0)):+.2f}%\n"
            f"⏰ Geç hareket cezası: -{float(result.get('late_penalty', 0)):.1f}\n"
            f"⚠️ Spike cezası: -{float(result.get('spike_penalty', 0)):.1f}\n"
            f"📉 Kırılım cezası: -{float(result.get('breakdown_penalty', 0)):.1f}\n"
            f"🔀 Diverjans cezası: -{float(result.get('divergence_penalty', 0)):.1f}\n\n"
            "🕯️ Sadece kapanmış mum kullanıldı.\n"
            "⚠️ Yatırım tavsiyesi değildir."
        )

        return self.send_message(message)
        
