import unittest

from app.forecast import forecast_demand


class ForecastTests(unittest.TestCase):
    def test_no_data(self):
        f = forecast_demand({}, {}, 9)
        self.assertEqual((f["value"], f["method"]), (0, "no_data"))

    def test_fallback_last_observed(self):
        f = forecast_demand({}, {7: 50, 8: 80}, 9)
        self.assertEqual(f["value"], 80)

    def test_moving_average(self):
        hist = {d: {9: 100} for d in range(1, 6)}
        self.assertEqual(forecast_demand(hist, {}, 9)["value"], 100)

    def test_trend_ratio_applied_and_clamped(self):
        hist = {d: {8: 100, 9: 100} for d in range(1, 4)}
        self.assertEqual(forecast_demand(hist, {8: 120}, 9)["value"], 120)
        self.assertEqual(forecast_demand(hist, {8: 1000}, 9)["value"], 130)  # 1.3 ilə məhdud
        self.assertEqual(forecast_demand(hist, {8: 1}, 9)["value"], 70)      # 0.7 ilə məhdud

    def test_no_lookahead(self):
        hist = {d: {8: 100, 9: 100} for d in range(1, 4)}
        a = forecast_demand(hist, {8: 100}, 9)["value"]
        b = forecast_demand(hist, {8: 100, 9: 5000, 10: 5000}, 9)["value"]  # gələcək dəyərlər təsir etməməlidir
        self.assertEqual(a, b)

    def test_uses_only_last_n_days(self):
        hist = {1: {9: 100}, 2: {9: 100}, 30: {9: 10_000}}
        self.assertEqual(forecast_demand(hist, {}, 9, max_days=2)["value"], 100)

    def test_missing_hour_in_some_days(self):
        hist = {1: {9: 100}, 2: {}, 3: {9: 200}}
        self.assertEqual(forecast_demand(hist, {}, 9)["value"], 150)


if __name__ == "__main__":
    unittest.main()
