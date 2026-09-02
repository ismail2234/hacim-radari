## 2026-09-02 - Avoid `statistics.mean` in Python 3.12+ Hot Loops
**Learning:** In Python 3.12+, `statistics.mean` converts numbers into exact fractions, causing a ~40x slowdown compared to `sum(xs) / len(xs)`. In candle feature calculation loops that compute averages for RSI, volume ratios, and historical backtests, this introduced significant overhead.
**Action:** Use `sum(xs) / len(xs)` (or helper `mean_safe`) for float sequences in performance-critical code paths.
