## 2025-05-18 - Vectorized True Range with NumPy np.fmax
**Learning:** Replacing Pandas `pd.concat([...], axis=1).max(axis=1)` with vectorized NumPy 1D array operations (`np.fmax`) eliminates heavy DataFrame construction overhead, speeding up True Range calculations by ~4x while preserving `NaN` handling.
**Action:** Use `np.fmax` on 1D NumPy arrays (`.to_numpy()`) instead of `pd.concat` when computing row-wise maxima across a small fixed set of Series.
