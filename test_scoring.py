import unittest
from unittest.mock import MagicMock
from scoring import (
    num, parse, safe_rsi, streak, klines, analyze, rank_signals, BAD_SYMBOLS
)

class TestScoring(unittest.TestCase):

    def test_num(self):
        self.assertEqual(num("12.5"), 12.5)
        self.assertEqual(num(10), 10.0)
        self.assertEqual(num("invalid", 5.0), 5.0)

    def test_parse_dict_and_list(self):
        dict_data = [
            {"high": "10", "low": "5", "close": "8", "volume": "100"},
            {"h": "12", "l": "6", "c": "9", "v": "150"},
        ]
        h, l, c, v = parse(dict_data)
        self.assertEqual(h, [10.0, 12.0])
        self.assertEqual(l, [5.0, 6.0])
        self.assertEqual(c, [8.0, 9.0])
        self.assertEqual(v, [100.0, 150.0])

        list_data = [
            [0, 0, "10", "5", "8", "100"],
            [0, 0, "12", "6", "9", "150"],
        ]
        h2, l2, c2, v2 = parse(list_data)
        self.assertEqual(h2, [10.0, 12.0])
        self.assertEqual(l2, [5.0, 6.0])
        self.assertEqual(c2, [8.0, 9.0])
        self.assertEqual(v2, [100.0, 150.0])

    def test_safe_rsi(self):
        closes = [10.0 + i * 0.5 for i in range(20)]
        val = safe_rsi(closes)
        self.assertIsInstance(val, float)

        # Failure case fallback
        self.assertEqual(safe_rsi(None), 50.0)

    def test_streak(self):
        dbs = MagicMock()
        dbs.get_last_signal.return_value = None
        self.assertEqual(streak(dbs, "BTCUSDT"), 1)

        dbs.get_last_signal.return_value = {"streak": 2}
        self.assertEqual(streak(dbs, "BTCUSDT"), 3)

        dbs.get_last_signal.return_value = {"streak": 10}
        self.assertEqual(streak(dbs, "BTCUSDT"), 9)

        dbs.get_last_signal.side_effect = Exception("DB error")
        self.assertEqual(streak(dbs, "BTCUSDT"), 1)

    def test_klines(self):
        client = MagicMock()
        client.klines.return_value = ["candle1", "candle2"]
        res = klines(client, "BTCUSDT", 100)
        self.assertEqual(res, ["candle1", "candle2"])

        # Test fallback keyword args signature
        client.klines.side_effect = [TypeError(), ["candle1"]]
        res2 = klines(client, "BTCUSDT", 100)
        self.assertEqual(res2, ["candle1"])

        # Test exception fallback
        client.klines.side_effect = Exception()
        res3 = klines(client, "BTCUSDT", 100)
        self.assertEqual(res3, [])

    def test_analyze_bad_symbol_or_empty(self):
        cfg = MagicMock()
        client = MagicMock()
        dbs = MagicMock()
        market = MagicMock()

        # Empty symbol
        self.assertIsNone(analyze(cfg, client, dbs, market, {}))

        # Bad symbol
        self.assertIsNone(analyze(cfg, client, dbs, market, {"symbol": "USDTTRY"}))

        # No klines
        client.klines.return_value = []
        self.assertIsNone(analyze(cfg, client, dbs, market, {"symbol": "BTCTRY"}))

    def test_analyze_valid_data(self):
        cfg = MagicMock()
        cfg.candles = 300
        client = MagicMock()
        dbs = MagicMock()
        dbs.get_last_signal.return_value = None
        market = MagicMock()

        # Generate 300 candles of mock data
        candles = []
        base_price = 100.0
        for i in range(300):
            p = base_price + (i % 5) * 0.2
            candles.append([0, 0, str(p + 1), str(p - 1), str(p), "1000"])

        client.klines.return_value = candles

        res = analyze(cfg, client, dbs, market, {"symbol": "BTCTRY"})
        self.assertIsNotNone(res)
        self.assertEqual(res["symbol"], "BTCTRY")
        self.assertIn("status", res)
        self.assertIn("score", res)

    def test_rank_signals(self):
        signals = [
            {"symbol": "A", "status": "PASS", "kivrim_early_score": 50, "kivrim_score": 50, "score": 50},
            {"symbol": "B", "status": "VERY", "kivrim_early_score": 80, "kivrim_score": 80, "score": 80},
            {"symbol": "C", "status": "BUY", "kivrim_early_score": 70, "kivrim_score": 70, "score": 70},
            {"symbol": "D", "status": "ONCU", "kivrim_early_score": 60, "kivrim_score": 60, "score": 60},
        ]
        ranked = rank_signals(signals)
        symbols = [s["symbol"] for s in ranked]
        self.assertEqual(symbols, ["B", "C", "D", "A"])

if __name__ == "__main__":
    unittest.main()
