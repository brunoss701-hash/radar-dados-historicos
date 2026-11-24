import unittest

import pandas as pd

from backtest import gate, simulate_trade


class BacktestLogicTests(unittest.TestCase):
    def test_gate_requires_full_and_oos_consistency(self):
        all_metrics = {
            "ALL": {},
            "trend_breakout:long": {
                "trades": 100, "avg_r": 0.10, "profit_factor": 1.30,
                "max_drawdown_r": -8.0,
            },
            "trend_breakout:short": {
                "trades": 100, "avg_r": -0.10, "profit_factor": 0.80,
                "max_drawdown_r": -10.0,
            },
        }
        oos_metrics = {
            "ALL": {},
            "trend_breakout:long": {
                "trades": 30, "avg_r": 0.08, "profit_factor": 1.20,
                "max_drawdown_r": -6.0,
            },
            "trend_breakout:short": {
                "trades": 30, "avg_r": 0.40, "profit_factor": 1.90,
                "max_drawdown_r": -5.0,
            },
        }
        breadth_all = {
            "trend_breakout:long": {
                "symbols_with_min_trades": 12, "positive_share": 0.67
            },
            "trend_breakout:short": {
                "symbols_with_min_trades": 12, "positive_share": 0.67
            },
        }
        breadth_oos = {
            "trend_breakout:long": {
                "symbols_with_min_trades": 10, "positive_share": 0.60
            },
            "trend_breakout:short": {
                "symbols_with_min_trades": 10, "positive_share": 0.60
            },
        }
        result = gate(all_metrics, oos_metrics, breadth_all, breadth_oos)
        self.assertEqual(result["trend_breakout:long"]["status"], "PASS_CANDIDATE")
        self.assertEqual(result["trend_breakout:short"]["status"], "FAIL_OR_INSUFFICIENT")

    def test_same_bar_target_and_stop_counts_as_stop(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="D")
        df = pd.DataFrame({
            "open": [100.0, 100.0, 100.0],
            "high": [101.0, 108.0, 101.0],
            "low": [99.0, 95.0, 99.0],
            "close": [100.0, 101.0, 100.0],
            "atr14": [2.0, 2.0, 2.0],
        }, index=idx)
        setup = {
            "direction": "long",
            "stop_atr_mult": 1.5,
            "target_atr_mult": 3.0,
        }
        trade = simulate_trade(df, 0, setup, max_hold=2, slippage_bps=0)
        self.assertEqual(trade["outcome"], "stop")
        self.assertLess(trade["r"], 0)

    def test_entry_is_next_bar_open(self):
        idx = pd.date_range("2026-01-01", periods=3, freq="D")
        df = pd.DataFrame({
            "open": [90.0, 105.0, 106.0],
            "high": [101.0, 106.0, 107.0],
            "low": [89.0, 104.0, 105.0],
            "close": [100.0, 105.0, 106.0],
            "atr14": [2.0, 2.0, 2.0],
        }, index=idx)
        setup = {
            "direction": "long",
            "stop_atr_mult": 1.5,
            "target_atr_mult": 3.0,
        }
        trade = simulate_trade(df, 0, setup, max_hold=1, slippage_bps=0)
        self.assertEqual(trade["entry"], 105.0)


if __name__ == "__main__":
    unittest.main()
