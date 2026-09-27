# Bolt's Journal

## 2026-09-27 - Vectorized True Range Calculation and Array Conversion
**Learning:** `pd.concat` multi-column concatenation in technical indicator functions like ATR and ADX creates significant DataFrame creation overhead. Replacing `pd.concat` with 1D NumPy `np.fmax` on `.to_numpy()` arrays provides a ~2.3x speedup while preserving NaN handling. Additionally, converting Series to 1D NumPy arrays in loop-based cross detection (`find_recent_cross_up`) avoids `.iloc` indexing overhead (~2.7x speedup).
**Action:** Use `np.fmax` with `.to_numpy()` for row-wise maximums across 1D series and convert Pandas Series to NumPy arrays before tight loop iterations.
