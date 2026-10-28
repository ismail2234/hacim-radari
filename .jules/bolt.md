## 2025-05-18 - Vectorized NumPy True Range & `.to_numpy()` Series Scanning

**Learning:** Replacing Pandas `pd.concat` multi-column DataFrame concatenation in True Range calculations (`compute_atr`, `compute_adx`) with 1D vectorized NumPy `np.fmax` operations reduces calculation overhead from ~1.13ms to ~0.05ms per call (~23x speedup). Additionally, converting Pandas Series to NumPy arrays (`.to_numpy()`) prior to loop iterations in crossover functions (`find_recent_cross_up`) avoids heavy `.iloc` indexing overhead, giving a ~3.8x speedup. Explicitly casting boolean return values derived from NumPy comparisons with `bool()` prevents `numpy.bool_` identity check failures (`is True`).

**Action:** Prefer 1D vectorized NumPy operations (`np.fmax`, `np.roll`) for technical indicator row-wise calculations instead of DataFrame `pd.concat`, and always convert Pandas Series to NumPy arrays before indexing in loops.
