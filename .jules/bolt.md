## 2026-09-07 - Vectorized NumPy True Range calculation in technical indicators
**Learning:** Replacing Pandas `pd.concat([...], axis=1).max(axis=1)` with vectorized 1D NumPy operations using `np.fmax`, `np.abs`, and `np.roll` on underlying array representations (`.to_numpy()`) yields a ~5.6x speedup on True Range calculations in ATR and ADX.
**Action:** When computing multi-column elementwise max across shifted Series, extract NumPy arrays and compute elementwise maximums using `np.fmax`.
