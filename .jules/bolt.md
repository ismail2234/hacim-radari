# Bolt's Journal

## 2025-05-18 - Technical Indicator Vectorization & Pandas Overhead

**Learning:** Replacing Pandas DataFrame operations (such as `pd.concat` multi-column max and Series `.iloc` indexing) with vectorized NumPy operations (`np.fmax` on `.to_numpy()` arrays) drastically improves technical indicator performance (e.g., ~6.7x speedup for ATR, ~8.9x speedup for breakout checks, ~4.6x speedup for anomaly detection).
**Action:** When computing indicators over time series data in Pandas DataFrames, extract 1D NumPy arrays via `.to_numpy()` for loop indexing and elementwise operations instead of DataFrame/Series methods.
