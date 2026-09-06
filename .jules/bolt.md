# Bolt's Journal - Critical Learnings

## 2025-05-20 - NumPy Slicing vs Pandas DataFrame Rolling Windows
**Learning:** Replacing Pandas `.rolling().mean()` calculations with NumPy slicing (`np.mean(arr[-window:])`) when only the latest window values are needed avoids allocating full Series/DataFrame columns across all rows for time-series evaluations, providing a ~50x speedup in evaluation loops.
**Action:** Always slice NumPy arrays directly when evaluating indicators on recent bars instead of running full rolling computations over the entire DataFrame.
