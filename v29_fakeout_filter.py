from __future__ import annotations

from dataclasses import dataclass


@dataclass
class V29FakeoutResult:
    penalty: int
    risk: str
    fakeout: bool
    reasons: list[str]


class V29FakeoutFilter:
    """
    Sahte kırılım ve sahte hacim filtresi.

    Amaç:
    Tek başına hacim patlamasını AL sinyali
    olarak kabul etmemektir.
    """

    def calculate(
        self,
        *,
        volume_ratio: float,
        price_change_pct: float,
        price_velocity: float,
        orderbook_imbalance: float,
        rsi_slope: float,
        close_above_ema7: bool,
        higher_low: bool,
    ) -> V29FakeoutResult:

        penalty = 0
        reasons: list[str] = []

        # Hacim çok yüksek ama fiyat hareket etmiyorsa
        # absorpsiyon veya sahte hareket olabilir.
        if (
            volume_ratio >= 5
            and abs(price_change_pct) < 0.10
        ):
            penalty += 25
            reasons.append(
                "Hacim yüksek fakat fiyat tepki vermiyor"
            )

        # Hacim artıyor ama fiyat aşağı gidiyorsa.
        if (
            volume_ratio >= 3
            and price_velocity < 0
        ):
            penalty += 25
            reasons.append(
                "Hacim artarken fiyat ivmesi negatif"
            )

        # Emir defteri satıcı baskılı.
        if orderbook_imbalance < -0.25:
            penalty += 20
            reasons.append(
                "Emir defteri satıcı baskılı"
            )

        # RSI momentum negatif.
        if rsi_slope < 0:
            penalty += 10
            reasons.append(
                "RSI momentum negatif"
            )

        # EMA7 geri alınmamış.
        if not close_above_ema7:
            penalty += 10
            reasons.append(
                "EMA7 henüz geri alınmadı"
            )

        # Higher-Low yok.
        if not higher_low:
            penalty += 10
            reasons.append(
                "Higher-Low teyidi yok"
            )

        penalty = max(
            0,
            min(100, penalty),
        )

        if penalty >= 60:
            risk = "YÜKSEK"

        elif penalty >= 35:
            risk = "ORTA"

        else:
            risk = "DÜŞÜK"

        return V29FakeoutResult(
            penalty=penalty,
            risk=risk,
            fakeout=penalty >= 35,
            reasons=reasons,
        )
      
