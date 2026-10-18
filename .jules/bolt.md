## 2026-10-17 - NumPy Vectorization for Technical Indicators

**Learning:** Replacing Pandas DataFrame/Series operations like `pd.concat` and `.iloc` loop lookups with 1D NumPy array operations (`.to_numpy()`, `np.fmax`, `np.isnan`) yields a ~6.6x speedup for ATR and ADX True Range calculations and ~3x speedup for crossover detection while preserving NaN handling behavior.

**Action:** When computing True Range or doing sequential window checks on price series, convert input series to NumPy arrays and perform vectorized element-wise comparisons (`np.fmax`, `np.abs`) instead of multi-column Pandas concatenations.
