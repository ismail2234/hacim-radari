## 2025-05-18 - Avoid O(N^2) Repeated Prefix Slice Calculations in Financial Indicators

**Learning:** Recomputing indicator series like Exponential Moving Averages (EMA) by calling an EMA helper on slices `values[:i+1]` in a loop creates an $O(N^2)$ bottleneck. For a standard window of 300 candles, this requires over 45,000 inner iterations per MACD calculation.

**Action:** Compute series indicators incrementally in a single $O(N)$ pass (`_ema_series`) when full historical series of sub-indicators are needed for downstream signals (such as MACD signal line).
