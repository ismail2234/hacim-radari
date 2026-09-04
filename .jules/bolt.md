## 2025-05-18 - Direct NumPy array slicing over Pandas DataFrame rolling windows
**Learning:** Calling `df['col'].rolling(window).mean()` computes a rolling calculation across the entire DataFrame for every coin evaluation. Slicing trailing elements of NumPy arrays (`np.mean(close[-window:])`) avoids Pandas Series creation and DataFrame index overhead, running ~22x faster (reducing execution time from ~3.97s to ~0.17s per 1000 evaluations).
**Action:** Use direct NumPy slicing and array operations (`np.mean`, `np.diff`, `np.maximum`) when evaluating fixed trailing windows on time-series data.
