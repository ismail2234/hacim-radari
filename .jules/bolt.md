# Bolt Journal - Performance Learnings

## 2026-09-07 - Vectorized NumPy for Technical Indicators
**Learning:** Using `pd.concat` for True Range computation across columns in ATR/ADX generates massive DataFrame instantiation overhead in hot scanning loops. Switching to `np.maximum` and NumPy diff array operations yields 2.5x - 6x speedups for `compute_atr` and `compute_adx`.
**Action:** Prefer NumPy array operations for pointwise min/max and difference calculations over pandas multi-column concat.
