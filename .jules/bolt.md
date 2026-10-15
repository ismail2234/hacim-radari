## 2025-02-18 - Vectorizing True Range calculations with NumPy np.fmax

**Learning:** Using `pd.concat([...], axis=1).max(axis=1)` to compute True Range across multiple Series creates huge Pandas DataFrame allocation overhead (~13x slower). Replacing multi-column concatenation with 1D NumPy array operations (`np.fmax`) eliminates memory allocations and speeds up ATR/ADX calculation significantly. Additionally, returning explicit Python `bool()` instead of `numpy.bool_` prevents pytest `is True` identity assertion failures.

**Action:** Prefer 1D NumPy array operations over multi-column Pandas DataFrame operations in indicator calculations, and explicitly cast boolean return values to `bool()`.
