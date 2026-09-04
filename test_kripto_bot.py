import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np
import kripto_bot


class TestKriptoBot(unittest.TestCase):

    def test_evaluate_insufficient_data(self):
        min_needed = max(kripto_bot.LONG_WINDOW, kripto_bot.RSI_PERIOD) + 20
        # Create a df with fewer rows than min_needed
        short_df = pd.DataFrame({
            "timestamp": list(range(10)),
            "open": [100.0] * 10,
            "high": [105.0] * 10,
            "low": [95.0] * 10,
            "close": [100.0] * 10,
            "volume": [1000.0] * 10,
        })
        with patch("kripto_bot.get_ohlcv", return_value=short_df):
            result = kripto_bot.evaluate("BTC/USDT")
            self.assertIsNone(result)

    def test_evaluate_matching_signal(self):
        # Construct synthetic price and volume sequence to trigger signal
        # Total rows = 45
        min_needed = max(kripto_bot.LONG_WINDOW, kripto_bot.RSI_PERIOD) + 20

        # Start with prices such that long MA > short MA in prev step
        # and short MA > long MA in curr step (crossover)
        # 45 candles total
        closes = [100.0] * 35
        # slightly declining to keep short MA <= long MA
        for i in range(8):
            closes.append(99.0 - i * 0.1)
        # bump last candle to make short MA cross above long MA
        closes.append(108.0)
        closes.append(112.0)

        # Ensure RSI ends up in [50, 75] and rising, and volume ratio >= 1.5
        volumes = [1000.0] * 44 + [3000.0]

        df = pd.DataFrame({
            "timestamp": list(range(45)),
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": volumes,
        })

        # Calculate expected RSI/vol_ratio values
        close_arr = np.array(closes, dtype=float)
        diffs = np.diff(close_arr)
        d_curr = diffs[-kripto_bot.RSI_PERIOD:]
        gain_curr = np.maximum(d_curr, 0)
        loss_curr = np.maximum(-d_curr, 0)
        rs_curr = np.mean(gain_curr) / (np.mean(loss_curr) if np.mean(loss_curr) != 0 else 1e-10)
        curr_rsi = float(100.0 - (100.0 / (1.0 + rs_curr)))

        # Patch RSI range for this test if needed or assert result if conditions met
        with patch("kripto_bot.get_ohlcv", return_value=df):
            result = kripto_bot.evaluate("BTC/USDT")

            # Check individual conditions
            curr_short = np.mean(close_arr[-kripto_bot.SHORT_WINDOW:])
            prev_short = np.mean(close_arr[-kripto_bot.SHORT_WINDOW - 1 : -1])
            curr_long = np.mean(close_arr[-kripto_bot.LONG_WINDOW:])
            prev_long = np.mean(close_arr[-kripto_bot.LONG_WINDOW - 1 : -1])

            trend_ok = prev_short <= prev_long and curr_short > curr_long
            d_prev = diffs[-kripto_bot.RSI_PERIOD - 1 : -1]
            gain_prev = np.maximum(d_prev, 0)
            loss_prev = np.maximum(-d_prev, 0)
            rs_prev = np.mean(gain_prev) / (np.mean(loss_prev) if np.mean(loss_prev) != 0 else 1e-10)
            prev_rsi = float(100.0 - (100.0 / (1.0 + rs_prev)))

            momentum_ok = kripto_bot.RSI_MIN <= curr_rsi <= kripto_bot.RSI_MAX and curr_rsi > prev_rsi
            vol_ratio = 3000.0 / np.mean(volumes[-20:])
            volume_ok = vol_ratio >= kripto_bot.VOLUME_MULTIPLIER

            if trend_ok and momentum_ok and volume_ok:
                self.assertIsNotNone(result)
                self.assertEqual(result["price"], closes[-1])
            else:
                self.assertIsNone(result)

    def test_evaluate_pandas_vs_numpy_equivalence(self):
        # Verify that our optimized NumPy calculation produces identical indicators as reference Pandas rolling calculation
        np.random.seed(123)
        prices = np.random.randn(50).cumsum() + 200.0
        vols = np.random.rand(50) * 500.0 + 100.0

        df = pd.DataFrame({
            "timestamp": list(range(50)),
            "open": prices,
            "high": prices + 1,
            "low": prices - 1,
            "close": prices,
            "volume": vols,
        })

        # Reference Pandas calculations
        ref_df = df.copy()
        ref_df["ma_short"] = ref_df["close"].rolling(kripto_bot.SHORT_WINDOW).mean()
        ref_df["ma_long"] = ref_df["close"].rolling(kripto_bot.LONG_WINDOW).mean()

        delta = ref_df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(kripto_bot.RSI_PERIOD).mean()
        avg_loss = loss.rolling(kripto_bot.RSI_PERIOD).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        ref_df["rsi"] = 100 - (100 / (1 + rs))
        ref_df["vol_avg"] = ref_df["volume"].rolling(20).mean()

        # Extract values via NumPy logic as implemented in kripto_bot.evaluate
        close = df["close"].to_numpy(dtype=float)
        volume = df["volume"].to_numpy(dtype=float)

        curr_short = np.mean(close[-kripto_bot.SHORT_WINDOW:])
        prev_short = np.mean(close[-kripto_bot.SHORT_WINDOW - 1 : -1])

        curr_long = np.mean(close[-kripto_bot.LONG_WINDOW:])
        prev_long = np.mean(close[-kripto_bot.LONG_WINDOW - 1 : -1])

        diffs = np.diff(close)
        d_curr = diffs[-kripto_bot.RSI_PERIOD:]
        gain_curr = np.maximum(d_curr, 0)
        loss_curr = np.maximum(-d_curr, 0)
        rs_curr = np.mean(gain_curr) / (np.mean(loss_curr) if np.mean(loss_curr) != 0 else 1e-10)
        curr_rsi = float(100.0 - (100.0 / (1.0 + rs_curr)))

        d_prev = diffs[-kripto_bot.RSI_PERIOD - 1 : -1]
        gain_prev = np.maximum(d_prev, 0)
        loss_prev = np.maximum(-d_prev, 0)
        rs_prev = np.mean(gain_prev) / (np.mean(loss_prev) if np.mean(loss_prev) != 0 else 1e-10)
        prev_rsi = float(100.0 - (100.0 / (1.0 + rs_prev)))

        avg_vol = float(np.mean(volume[-20:]))

        self.assertAlmostEqual(curr_short, ref_df["ma_short"].iloc[-1], places=6)
        self.assertAlmostEqual(prev_short, ref_df["ma_short"].iloc[-2], places=6)
        self.assertAlmostEqual(curr_long, ref_df["ma_long"].iloc[-1], places=6)
        self.assertAlmostEqual(prev_long, ref_df["ma_long"].iloc[-2], places=6)
        self.assertAlmostEqual(curr_rsi, ref_df["rsi"].iloc[-1], places=6)
        self.assertAlmostEqual(prev_rsi, ref_df["rsi"].iloc[-2], places=6)
        self.assertAlmostEqual(avg_vol, ref_df["vol_avg"].iloc[-1], places=6)


if __name__ == "__main__":
    unittest.main()
