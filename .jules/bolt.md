# Bolt's Journal - Critical Learnings

## 2026-09-07 - Replace pd.concat with np.fmax in True Range computation
**Learning:** `pd.concat([s1, s2, s3], axis=1).max(axis=1)` in ATR and ADX creates temporary 2D DataFrames with multi-column indexing overhead. Replacing `pd.concat` with `np.fmax` on Series/NumPy arrays speeds up True Range calculations by ~3-4x.
**Action:** Use `np.fmax` for element-wise maximum over pandas Series or 1D arrays instead of `pd.concat(..., axis=1).max(axis=1)`.
