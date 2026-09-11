# Bolt's Journal - Critical Learnings

## 2025-05-18 - Vectorized True Range NumPy Optimization
**Learning:** In technical indicator functions like `compute_atr` and `compute_adx`, calculating True Range via `pd.concat([...], axis=1).max(axis=1)` builds a multi-column DataFrame and performs row-wise max operations, introducing significant overhead.
**Action:** Use 1D NumPy arrays (`.to_numpy()`) with `np.fmax`, `np.abs`, and `np.roll` in `_compute_true_range`. Always check for empty DataFrames before array indexing (`if df.empty:`).
