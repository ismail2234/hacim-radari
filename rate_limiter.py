
"""
Eski koddaki en somut operasyonel risk buradaydı: 80 sembol x 2 kline
çağrısı (~160 istek), 10 thread ile paralel, 30 saniyede bir tekrarlanıyor
-- hiçbir ağırlık/hız kontrolü olmadan. Binance IP başına dakikalık ağırlık
limiti uygular; bunu aşmak IP ban ile sonuçlanabilir (418/429 sonrası
süresi artan banlar).

Bu modül basit bir token-bucket: her istek tahmini bir "ağırlık" tüketir,
bütçe dakikada resetlenir. Bütçe dolduğunda `acquire()` bloklar (kısa
sürelerle) ve thread'i bekletir -- exception fırlatmaz, sadece yavaşlatır.

Not: Binance'in gerçek ağırlık tablosunu birebir yansıtmıyoruz (endpoint'e
göre değişir); amaç kaba ama etkili bir üst sınır koymak. Gerekirse
WEIGHT_BUDGET_PER_MINUTE düşürülüp yükseltilebilir.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, budget_per_minute: int):
        self._budget = budget_per_minute
        self._remaining = budget_per_minute
        self._window_start = time.monotonic()
        self._lock = threading.Lock()

    def _maybe_reset(self) -> None:
        now = time.monotonic()
        if now - self._window_start >= 60:
            self._remaining = self._budget
            self._window_start = now

    def acquire(self, weight: int = 1) -> None:
        """Yeterli bütçe oluşana kadar bloklar, sonra tüketir."""
        while True:
            with self._lock:
                self._maybe_reset()
                if self._remaining >= weight:
                    self._remaining -= weight
                    return
                wait_for = 60 - (time.monotonic() - self._window_start)

            time.sleep(max(0.05, min(wait_for, 2.0)))

    def snapshot(self) -> dict:
        with self._lock:
            self._maybe_reset()
            return {
                "budget": self._budget,
                "remaining": self._remaining,
                "window_age_s": round(time.monotonic() - self._window_start, 1),
            }
          
