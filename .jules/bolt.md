## 2026-10-22 - Replacing `pd.concat` multi-column max with vectorized NumPy `np.fmax`

**Learning:** In technical indicator computations like ATR and ADX True Range, constructing multi-column DataFrames using `pd.concat` followed by `.max(axis=1)` incurs substantial overhead due to DataFrame block management. Converting 1D Series to NumPy arrays (`.to_numpy()`) and applying vectorized `np.fmax(hl, np.fmax(hpc, lpc))` eliminates DataFrame allocation overhead while maintaining NaN skipping behavior.
**Action:** Use vectorized `np.fmax` on 1D NumPy arrays when calculating elementwise max across time-series arrays.

## 2026-10-22 - Replacing Pandas `.iloc` loop indexing with `.to_numpy()` array indexing

**Learning:** Indexing Pandas Series with `.iloc[i]` inside loop iterations (e.g. crossover lookback loops) adds high indexing overhead per step. Converting Series objects to NumPy arrays (`.to_numpy()`) prior to loop execution speeds up search loops by ~2.6x to ~3.7x.
**Action:** Always convert Pandas Series to NumPy 1D arrays (`.to_numpy()`) before indexing inside loop structures.
