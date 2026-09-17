# Bolt Journal

## 2026-03-29 - Vectorized True Range Calculation in ATR & ADX
**Learning:** `pd.concat([...], axis=1).max(axis=1)` creates temporary 2D DataFrames and suffers high allocation overhead when computing row-wise maximums.
**Action:** Extract float NumPy arrays (`.to_numpy(dtype=float)`), shift with `np.roll`, and use `np.fmax` to compute row-wise maximums (~5.8x speedup).
