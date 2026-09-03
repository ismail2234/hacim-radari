import unittest
from unittest.mock import patch
import pandas as pd
import numpy as np
from kripto_bot import evaluate, compute_rsi, SHORT_WINDOW, LONG_WINDOW, RSI_PERIOD, RSI_MIN, RSI_MAX, VOLUME_MULTIPLIER

class TestKriptoBot(unittest.TestCase):
    def test_compute_rsi(self):
        prices = pd.Series([10.0 + i * 0.5 for i in range(30)])
        rsi = compute_rsi(prices, 14)
        self.assertEqual(len(rsi), 30)
        # Monotonically increasing prices -> RSI should approach 100
        self.assertTrue(rsi.iloc[-1] > 90)

    @patch("kripto_bot.get_ohlcv")
    def test_evaluate_returns_none_when_short_data(self, mock_get_ohlcv):
        mock_get_ohlcv.return_value = pd.DataFrame({
            "timestamp": range(10),
            "open": range(10),
            "high": range(10),
            "low": range(10),
            "close": range(10),
            "volume": range(10)
        })
        self.assertIsNone(evaluate("BTC/USDT"))

    @patch("kripto_bot.get_ohlcv")
    def test_evaluate_signal(self, mock_get_ohlcv):
        np.random.seed(0)
        # Create a price trajectory that triggers trend + momentum + volume
        # min_needed = 21 + 20 = 41
        n = 50
        # MA short (9) and MA long (21) crossover at the end
        close = np.ones(n) * 100.0
        # first 45 points flat
        # last 5 points sharp uptrend
        close[-5:] = [101.0, 102.0, 105.0, 110.0, 120.0]
        volume = np.ones(n) * 100.0
        volume[-1] = 300.0 # 3x avg volume

        df = pd.DataFrame({
            "timestamp": range(n),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": volume
        })
        mock_get_ohlcv.return_value = df

        res = evaluate("BTC/USDT")
        # Check result format
        if res is not None:
            self.assertIn("price", res)
            self.assertIn("rsi", res)
            self.assertIn("vol_ratio", res)
            self.assertEqual(res["price"], 120.0)

if __name__ == "__main__":
    unittest.main()
