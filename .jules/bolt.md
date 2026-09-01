## 2025-02-23 - Avoid `statistics.mean` in hot loops on Python 3.12+
**Learning:** In Python 3.12+, `statistics.mean` converts floats to `fractions.Fraction` to prevent intermediate precision loss, incurring up to ~80x execution overhead compared to floating point division (`sum(xs) / len(xs)`).
**Action:** Use `sum(values) / len(values)` (or helper `mean_safe(values)`) instead of `statistics.mean` for floating point mean computations in tight numerical/signal analysis loops.
