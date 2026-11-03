# Bolt's Journal - Critical Learnings

## 2026-09-07 - Vectorized True Range Calculation via NumPy fmax
**Learning:** Constructing intermediate multi-column Pandas DataFrames via `pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)` introduces substantial Pandas indexing, alignment, and object-creation overhead during technical indicator calculations (such as ATR and ADX). Shifting the close series via `np.full_like(close, np.nan, dtype=np.float64)` and evaluating True Range with nested `np.fmax` operations on NumPy float64 arrays is ~6.5x faster while correctly skipping NaN on the leading element.
**Action:** When computing multi-series elementwise maximums or differences for technical indicators, operate directly on NumPy arrays (`.to_numpy(dtype=np.float64)`) using `np.fmax` and wrap back to `pd.Series` only when rolling/ewm operations require time-series indexing.
