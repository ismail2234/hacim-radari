## 2026-03-30 - Vectorize True Range calculation with NumPy
**Learning:** `pd.concat` with `axis=1` to find the row-wise max across multiple Series in Pandas incurs significant overhead due to DataFrame construction and index alignment. Vectorizing with 1D NumPy arrays (`.to_numpy()`) and `np.fmax` speeds up True Range calculations by ~5.8x.
**Action:** Replace `pd.concat([...], axis=1).max(axis=1)` with NumPy `np.fmax` on 1D arrays for technical indicators like ATR and ADX.
