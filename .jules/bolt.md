## 2025-05-18 - Vectorized True Range (TR) Calculation
**Learning:** Replacing multi-column Pandas DataFrame concatenation (`pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)`) with vectorized NumPy array comparisons using `np.fmax(tr1, np.fmax(tr2, tr3))` yields a ~2x speedup in True Range computation for technical indicators like ATR and ADX, while preserving exact NaN skipping behavior on the initial row.
**Action:** Use vectorized `np.fmax` on 1D numpy arrays extracted via `.to_numpy()` when calculating row-wise maximums across multiple series in Pandas.
