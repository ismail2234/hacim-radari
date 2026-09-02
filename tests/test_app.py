import unittest
import app

class TestAppCalculations(unittest.TestCase):

    def test_mean_safe(self):
        self.assertEqual(app.mean_safe([]), 0.0)
        self.assertEqual(app.mean_safe([], default=10.0), 10.0)
        self.assertEqual(app.mean_safe([1, 2, 3, 4, 5]), 3.0)
        self.assertEqual(app.mean_safe((10, 20)), 15.0)
        # Test with generator
        gen = (x for x in [2, 4, 6])
        self.assertEqual(app.mean_safe(gen), 4.0)

    def test_rsi_series(self):
        values = [10.0 + i * 0.5 for i in range(30)]
        rsi = app.rsi_series(values, 14)
        self.assertEqual(len(rsi), 30)
        # For strictly increasing values, RSI should be 100.0 at period 14
        self.assertAlmostEqual(rsi[-1], 100.0)

    def test_calculate_features_and_v33_score(self):
        candles = []
        base_price = 100.0
        for i in range(100):
            candles.append({
                "time": 1700000000000 + i * 300000,
                "open": base_price + (i % 5),
                "high": base_price + (i % 5) + 2.0,
                "low": base_price + (i % 5) - 1.0,
                "close": base_price + (i % 5) + 0.5,
                "volume": 1000.0 + i * 10,
                "quote_volume": 100000.0 + i * 1000,
                "close_time": 1700000000000 + (i + 1) * 300000 - 1,
            })

        features = app.calculate_features(candles, 80)
        self.assertIsNotNone(features)
        self.assertIn("price", features)
        self.assertIn("rsi14", features)

        score_res = app.v33_score(candles, 80)
        self.assertIsNotNone(score_res)
        self.assertIn("score", score_res)
        self.assertIn("status", score_res)

if __name__ == "__main__":
    unittest.main()
