## 2026-09-22 - Vectorized 1D NumPy calculations for indicators
**Learning:** In pandas indicator calculations, converting Series/DataFrames to NumPy 1D arrays (`to_numpy()`) and using `np.fmax` or `np.diff` drastically improves evaluation speed (~2.3x to 5.8x speedup) compared to multi-column `pd.concat().max(axis=1)` or `.iloc` loop indexing.
**Action:** Always convert Series to NumPy arrays before tight loops or element-wise max/diff operations on technical indicators.
