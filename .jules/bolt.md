## 2026-03-30 - Vectorize True Range and DM calculations in ATR / ADX

**Learning:** `pd.concat` across multiple Series to compute element-wise maximums (`axis=1`) introduces significant object creation and index alignment overhead. Using 1D NumPy arrays with `np.fmax` yields ~3.3x speedup for `compute_atr` and ~2.0x speedup for `compute_adx`. Furthermore, comparisons returned from Pandas/NumPy operations return `numpy.bool_` objects which fail strict `is True` identity assertions unless explicitly converted via `bool()`.
**Action:** Replace `pd.concat([s1, s2, ...], axis=1).max(axis=1)` with 1D NumPy `.to_numpy()` and `np.fmax(...)`. Always cast comparison function outputs to standard Python `bool(...)`.
