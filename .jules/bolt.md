## 2025-03-01 - Vectorize row-wise Pandas operations with NumPy element-wise functions

**Learning:** In Pandas feature generation pipelines, row-wise operations like `df[['a', 'b']].max(axis=1)` or `.min(axis=1)` introduce significant Python iteration and Series construction overhead (~1.05ms for 100 rows). Using `np.maximum(df['a'].to_numpy(), df['b'].to_numpy())` or passing Series directly to NumPy functions executes purely in C/vectorized memory (~0.38ms for 100 rows), yielding a ~2.7x-3x speedup.
**Action:** When calculating upper/lower wicks, candle ranges, or min/max bounds across pairs of columns in Pandas DataFrames, convert to NumPy arrays or use element-wise NumPy functions instead of `axis=1` DataFrame reductions.
