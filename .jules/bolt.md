## 2025-05-18 - Pandas Indicator Concatenation vs Sequential Column Assignment

**Learning:** Assigning technical indicator columns one-by-one (`df["col"] = ...`) or calling functions that each perform `df.copy()` triggers repeated Pandas BlockManager memory reallocations and DataFrame copies (5+ full copies per `add_indicators` call). Collecting calculated Series in a python dictionary and concatenating once with `pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)` speeds up execution by ~1.9x (~19.1 ms down to ~10.1 ms for 500 rows). Also, replacing `pd.cut().groupby().sum()` with `np.bincount()` in volume profiling removes heavy pandas grouping overhead.

**Action:** In Pandas-heavy feature engineering pipelines, compute all new columns as Series in a dictionary and concatenate in a single `pd.concat` step, and use NumPy vector operations for binning and aggregations.
