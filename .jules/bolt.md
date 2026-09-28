## 2026-09-07 - Vectorizing Pandas True Range Calculation with np.fmax

**Learning:** `pd.concat([...], axis=1).max(axis=1)` introduces significant Pandas DataFrame construction overhead when computing row-wise maxima over multiple shifted Series (such as True Range in ATR and ADX). Converting Series to NumPy arrays (`.to_numpy()`) and using `np.fmax` yields a ~5x speedup for ATR calculations while producing identical results.
**Action:** Use `np.fmax` over NumPy 1D arrays instead of `pd.concat` for row-wise maximum calculations across technical indicator Series.
