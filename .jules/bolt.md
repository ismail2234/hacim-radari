## 2026-10-29 - Vectorized True Range Calculation in ATR
**Learning:** In Pandas, multi-column Series concatenation followed by `.max(axis=1)` allocates temporary DataFrames and is ~5.7x slower than 1D NumPy array operations using `np.fmax`.
**Action:** Use 1D NumPy array conversions (`.to_numpy()`) and `np.fmax` when calculating True Range across high, low, and close series.
