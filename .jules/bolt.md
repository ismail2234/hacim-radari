## 2025-05-18 - In-place Indexing vs DataFrame Slicing in Backtests

**Learning:** Backtesting loops that slice and copy DataFrames (`df.iloc[:index+1].copy()`) at every step suffer massive CPU/memory overhead due to $O(N^2)$ DataFrame allocations. Pre-calculating features on the full DataFrame and passing an `idx` parameter with fast scalar lookup (`.iat[idx]`) avoids copying DataFrames completely.
**Action:** Always design analysis and signal engines to accept an optional candle `idx` parameter so backtesters can evaluate historical candles in-place.
