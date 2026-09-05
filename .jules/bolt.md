## 2026-03-31 - Pandas vs NumPy for time-series tail evaluations
**Learning:** For scanning hundreds of crypto symbols with small window sizes (e.g. 41 OHLCV rows), constructing Pandas DataFrames and calling `rolling().mean()` introduces massive overhead (~40x slower) compared to direct NumPy array slicing (`np.mean(arr[-window:])`).
**Action:** Use NumPy array slicing for tail-only rolling indicator computations when full time-series history is not required.
