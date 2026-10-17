## 2025-05-18 - Vectorizing Technical Indicators with NumPy

**Learning:** Replacing multi-column Pandas DataFrame concatenation (`pd.concat`) and row-wise maximums (`.max(axis=1)`) with 1D NumPy array operations (`np.fmax`, `np.abs`) in technical indicator functions like ATR and ADX provides a ~2.5x to ~3.4x speedup by eliminating DataFrame object creation and index alignment overhead. Converting Series objects to NumPy 1D arrays (`.to_numpy()`) prior to loop iterations also avoids expensive `.iloc` indexing lookups.

**Action:** When working on performance-critical time-series computations in Pandas, convert Series to 1D NumPy arrays and use `np.fmax` for vectorized max operations (which also preserves NaN handling).
