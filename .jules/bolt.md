# Bolt's Journal - Critical Learnings

## 2026-03-30 - NumPy np.fmax vectorization for True Range in technical indicators
**Learning:** Replacing multi-column Pandas DataFrame concatenation (`pd.concat([...], axis=1).max(axis=1)`) with vectorized 1D NumPy array calculations (`np.fmax`) in indicator functions such as ATR and ADX avoids heavy DataFrame construction and index alignment overhead. This yields a ~5x speedup for ATR calculations (0.27ms vs 1.43ms) and a ~1.4x speedup for ADX calculations without changing indicator outputs or breaking NaN handling.
**Action:** Always check technical indicator functions for `pd.concat` across rows/columns and convert `pd.Series` to NumPy arrays (`.to_numpy()`) for 1D element-wise max/min operations using `np.fmax`/`np.fmin`.
