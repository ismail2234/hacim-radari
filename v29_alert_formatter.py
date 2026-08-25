from __future__ import annotations

from typing import Any


class V29AlertFormatter:
    """
    V29 sinyalini Telegram/arayüz için metne çevirir.

    Erkenlik ve teyit skorlarını ayrı gösterir.
    """

    @staticmethod
    def format(
        result: dict[str, Any],
    ) -> str:

        symbol = str(
            result.get("symbol", "UNKNOWN")
        )

        price = float(
            result.get("price", 0)
        )

        score = int(
            result.get("score", 0)
        )

        early_score = int(
            result.get(
                "early_score",
                result.get(
                    "earlyness_score",
                    0,
                ),
            )
        )

        confirmation = int(
            result.get(
                "confirmation_score",
                0,
            )
        )

        status = str(
            result.get(
                "status",
                "BEKLE",
            )
        )

        volume_ratio = float(
            result.get(
                "volume_ratio",
                0,
            )
        )

        fake_risk = int(
            result.get(
                "fakeout_penalty",
                result.get(
                    "fake_risk",
                    0,
                ),
            )
        )

        lines = [
            "🐋 BALİNA RADARI V29",
            "",
            f"🪙 #{symbol.replace('_TRY', 'TRY')}",
            f"💰 {price:.8f}",
            "",
            f"🎯 V29 Skor: {score}/100",
            f"🌀 Durum: {status}",
            f"⚡ Erkenlik: {early_score}/100",
            f"✅ Teyit: {confirmation}/100",
            "",
            f"📊 Hacim: {volume_ratio:.2f}x",
            f"⚠️ Fakeout Riski: {fake_risk}/100",
        ]

        reasons = result.get(
            "reasons",
            [],
        )

        if reasons:
            lines.extend(
                [
                    "",
                    "🔎 Tespitler:",
                ]
            )

            for reason in reasons[:12]:
                lines.append(
                    f"• {reason}"
                )

        lines.extend(
            [
                "",
                "⚠️ Yatırım tavsiyesi değildir.",
            ]
        )

        return "\n".join(lines)
                  
