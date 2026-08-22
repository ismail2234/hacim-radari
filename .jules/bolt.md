## 2025-05-20 - O(n^2) MACD Calculation via Redundant Slice EMA
**Learning:** `indicators.macd()` was re-calling `ema(values[:i+1])` in every loop iteration over the closes array, causing an $O(n^2)$ time complexity overhead per candle sequence (~300 candles per candidate symbol).
**Action:** Always calculate cumulative EMA indicators incrementally in a single $O(n)$ loop rather than slicing array prefixes in a loop.
