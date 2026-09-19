## 2025-05-18 - Technical Indicator Vectorization and Series Access
**Learning:** In crypto indicator calculations executed frequently over multiple market dataframes, replacing `pd.concat` True Range calculations with `np.fmax` on 1D NumPy arrays (`.to_numpy()`) and replacing `.iloc` access in crossover loops with NumPy array indexing + `math.isnan` yields ~2.6x overall execution speedup.
**Action:** Always convert Pandas Series to 1D NumPy arrays before looping over elements or applying multi-column elementwise max calculations in technical indicator routines.
