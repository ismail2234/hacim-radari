# Bolt's Performance Journal

## 2026-09-07 - Pandas Series `.iloc` Loop Optimization
**Learning:** In Pandas, accessing series elements via `.iloc[i]` within a loop incurs substantial overhead due to indexing metadata checks and Series wrapper construction. Converting `Series` to 1D NumPy arrays via `.to_numpy()` before the loop yields ~3.7x speedup while preserving exact semantics.
**Action:** Always convert Pandas `Series` or `DataFrame` slices to NumPy arrays with `.to_numpy()` when performing index-based loop iterations in indicator calculations.
