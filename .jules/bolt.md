## 2025-05-20 - Vectorized NumPy operations for True Range (TR) calculations
**Learning:** Using `pd.concat([...], axis=1).max(axis=1)` to compute True Range across High, Low, and Close prices creates significant DataFrame construction and alignment overhead (~6x slower).
**Action:** Use vectorized NumPy arrays with `np.fmax(tr1, np.fmax(tr2, tr3))` and wrap back into a `pd.Series` with original index for rolling calculations.
