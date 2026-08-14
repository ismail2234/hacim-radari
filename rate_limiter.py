from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, budget_per_minute: int):
        if budget_per_minute <= 0:
            raise ValueError("budget_per_minute 0'dan büyük olmalı")

        self._budget = budget_per_minute
        self._remaining = budget_per_minute
        self._window_start = time.monotonic()
        self._lock = threading.Lock()

    def _reset_if_needed(self, now: float) -> None:
        elapsed = now - self._window_start

        if elapsed >= 60.0:
            self._window_start = now
            self._remaining = self._budget

    def acquire(self, weight: int = 1) -> None:
        if weight <= 0:
            return

        if weight > self._budget:
            raise ValueError(
                f"İstek ağırlığı ({weight}) dakikalık bütçeden "
                f"({self._budget}) büyük olamaz"
            )

        while True:
            with self._lock:
                now = time.monotonic()
                self._reset_if_needed(now)

                if self._remaining >= weight:
                    self._remaining -= weight
                    return

                wait_time = max(
                    0.05,
                    min(
                        2.0,
                        60.0 - (now - self._window_start),
                    ),
                )

            time.sleep(wait_time)

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            self._reset_if_needed(now)

            return {
                "budget": self._budget,
                "remaining": self._remaining,
                "used": self._budget - self._remaining,
                "window_age_s": round(
                    now - self._window_start,
                    1,
                ),
      }
