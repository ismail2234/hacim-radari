## 2026-03-30 - Vectorized True Range Calculation with np.fmax
**Learning:** In Pandas, computing elementwise maximum across multiple shifted/differenced series using `pd.concat([...], axis=1).max(axis=1)` incurs severe DataFrame construction overhead (~5x slower). Using `np.fmax` with NumPy arrays yields the exact same True Range (TR) results with significantly lower latency.
**Action:** Replace `pd.concat` column max with `np.fmax(a, np.fmax(b, c))` in technical indicators like ATR and ADX.
