## 2026-10-21 - Vectorized NumPy True Range vs Pandas Concat
**Learning:** Replacing Pandas `pd.concat` multi-column concatenation with vectorized NumPy operations (`np.fmax`) on 1D arrays (`.to_numpy()`) in technical indicator functions (ATR, ADX True Range) yields ~5.8x speedup while preserving NaN handling for the initial shift window.
**Action:** Use 1D NumPy `np.fmax` with shifted array initialization for multi-series element-wise max computations in Pandas DataFrames.
