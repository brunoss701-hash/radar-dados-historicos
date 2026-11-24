import unittest

from strategy_engine import evaluate_asset, evaluate_daily


class StrategyEngineTests(unittest.TestCase):
    def base_long(self):
        return {
            "close": 120.0,
            "ema20": 115.0,
            "ema50": 110.0,
            "ema200": 100.0,
            "rsi14": 60.0,
            "rsi_delta1": 2.0,
            "macd_hist_delta1": 0.4,
            "atr14": 4.0,
            "adx14": 30.0,
            "plus_di14": 28.0,
            "minus_di14": 12.0,
            "cmf20": 0.10,
            "volume_ratio": 1.4,
            "donchian_breakout20": "up",
            "distance_ema20_pct": 4.35,
        }

    def base_short(self):
        return {
            "close": 80.0,
            "ema20": 85.0,
            "ema50": 90.0,
            "ema200": 100.0,
            "rsi14": 40.0,
            "rsi_delta1": -2.0,
            "macd_hist_delta1": -0.4,
            "atr14": 3.0,
            "adx14": 31.0,
            "plus_di14": 11.0,
            "minus_di14": 29.0,
            "cmf20": -0.12,
            "volume_ratio": 1.3,
            "donchian_breakout20": "down",
            "distance_ema20_pct": -5.88,
        }

    def test_breakout_long_triggered_daily(self):
        setups = evaluate_daily(self.base_long())
        found = [x for x in setups if x["setup"] == "trend_breakout" and x["direction"] == "long"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["daily_trigger"])

    def test_breakout_short_triggered_daily(self):
        setups = evaluate_daily(self.base_short())
        found = [x for x in setups if x["setup"] == "trend_breakout" and x["direction"] == "short"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["daily_trigger"])

    def test_breakout_can_be_armed_without_trigger(self):
        d = self.base_long()
        d["donchian_breakout20"] = "none"
        setups = evaluate_daily(d)
        found = [x for x in setups if x["setup"] == "trend_breakout" and x["direction"] == "long"]
        self.assertEqual(found[0]["daily_state"], "ARMED")

    def test_intraday_confirms_trigger_and_builds_rr(self):
        intra = {
            "close": 121.0,
            "ema20": 119.0,
            "rsi14": 56.0,
            "macd_hist_delta1": 0.2,
            "plus_di14": 24.0,
            "minus_di14": 14.0,
            "atr14": 2.0,
        }
        setups = evaluate_asset(self.base_long(), intra)
        found = [x for x in setups if x["setup"] == "trend_breakout" and x["direction"] == "long"][0]
        self.assertEqual(found["state"], "TRIGGERED")
        self.assertAlmostEqual(found["risk_plan"]["rr_reference"], 2.0)

    def test_daily_trigger_waits_for_bad_intraday(self):
        intra = {
            "close": 117.0,
            "ema20": 119.0,
            "rsi14": 42.0,
            "macd_hist_delta1": -0.2,
            "plus_di14": 10.0,
            "minus_di14": 25.0,
            "atr14": 2.0,
        }
        setups = evaluate_asset(self.base_long(), intra)
        found = [x for x in setups if x["setup"] == "trend_breakout" and x["direction"] == "long"][0]
        self.assertEqual(found["state"], "DAILY_TRIGGER_PENDING_INTRADAY")

    def test_pullback_long(self):
        d = self.base_long()
        d.update({
            "close": 113.0,
            "ema20": 112.0,
            "ema50": 108.0,
            "ema200": 100.0,
            "rsi14": 53.0,
            "distance_ema20_pct": 0.89,
            "donchian_breakout20": "none",
            "volume_ratio": 0.9,
        })
        setups = evaluate_daily(d)
        found = [x for x in setups if x["setup"] == "trend_pullback" and x["direction"] == "long"]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["daily_trigger"])


if __name__ == "__main__":
    unittest.main()
