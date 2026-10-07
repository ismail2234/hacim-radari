## 2025-05-18 - Vectorized NumPy True Range for Technical Indicators
**Learning:** Replacing Pandas `pd.concat` multi-column DataFrames for True Range (TR) calculations with 1D NumPy `np.fmax` operations yields a ~6.8x speedup in ATR and ADX computation.
**Action:** Always prefer 1D NumPy `np.fmax` over `pd.concat([...], axis=1).max(axis=1)` when computing max values across series in time-series technical indicators.
