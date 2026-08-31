## 2026-03-31 - Optimize `statistics.mean` in `app.py`
**Learning:** `statistics.mean` converts numbers to `fractions.Fraction` for exact precision, making it ~35x slower per call than built-in `sum(xs) / len(xs)` in Python 3.12. In hot paths like signal scoring, this introduces significant CPU overhead.
**Action:** Use `sum(xs) / len(xs)` for numerical mean calculations in performance-sensitive loops.
