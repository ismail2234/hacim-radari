## 2026-09-18 - Vectorized NumPy Operations in Technical Indicator Functions

**Learning:** Replacing Pandas operations (`rolling().mean()`, `iloc` lookups in loops, multi-column `pd.concat`, `.max()`) with 1D NumPy array conversions (`.to_numpy()`), `np.convolve`, and `np.fmax` yields a ~2.6x overall speedup in `evaluate()` and up to 19x speedup in individual indicator functions like `compute_rsi()`.
- `compute_rsi()`: `np.convolve` on 1D delta arrays avoids Pandas Series `rolling().mean()` creation overhead (~19x speedup).
- `is_breakout()` and `detect_anomaly()`: NumPy slicing and `np.nanmax` on `.to_numpy()` arrays bypass Pandas indexing overhead (~5-8x speedup).
- `compute_atr()` and `compute_adx()`: Using `np.fmax` on 1D arrays avoids `pd.concat(..., axis=1)` multi-column DataFrame allocations (~1.4x to ~5.2x speedup).

**Action:** Convert Pandas Series/DataFrames to NumPy arrays (`.to_numpy()`) prior to calculations in frequently called indicator evaluation functions. Use `np.fmax` instead of `pd.concat` and `np.convolve` instead of `rolling().mean()` where possible.
