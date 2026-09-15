## 2026-09-15 - Vectorize True Range in Technical Indicators using NumPy
**Learning:** Computing True Range using `pd.concat([..., axis=1]).max(axis=1)` creates heavy multi-column DataFrame allocations and row-wise max operations in Pandas. Extracting 1D arrays with `.to_numpy()` and using `np.fmax` speeds up True Range calculation by ~6x and overall strategy evaluation by ~15%.
**Action:** Use `np.fmax` on 1D NumPy arrays instead of multi-column `pd.concat` when computing True Range or element-wise maximums across multiple series.
