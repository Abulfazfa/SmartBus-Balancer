import unittest

from app.engine import (LOAD_CAP, RouteInfo, classify, compute_metrics, recommend,
                        round_trip_minutes, system_summary)

R = lambda i, code="X", length=10, minb=2: RouteInfo(i, code, "t", length, 80, minb)


class MetricsTests(unittest.TestCase):
    def test_round_trip_positive(self):
        self.assertGreater(round_trip_minutes(10), 6)

    def test_invalid_length(self):
        with self.assertRaises(ValueError):
            round_trip_minutes(0)

    def test_zero_buses_no_zero_division(self):
        m = compute_metrics(R(1), 0, 100)
        self.assertEqual(m["load_factor"], LOAD_CAP)
        self.assertEqual(m["status"], "critical")
        self.assertIsNone(m["headway_min"])

    def test_zero_buses_zero_demand(self):
        m = compute_metrics(R(1), 0, 0)
        self.assertEqual(m["load_factor"], 0)
        self.assertEqual(m["wait_min"], 0)

    def test_negative_input_rejected(self):
        with self.assertRaises(ValueError):
            compute_metrics(R(1), -1, 10)
        with self.assertRaises(ValueError):
            compute_metrics(R(1), 1, -10)

    def test_empty_route_is_idle(self):
        self.assertEqual(compute_metrics(R(1), 3, 0)["status"], "idle")

    def test_classify_thresholds(self):
        self.assertEqual(classify(0.39), "idle")
        self.assertEqual(classify(0.40), "ok")
        self.assertEqual(classify(0.74), "ok")
        self.assertEqual(classify(0.75), "busy")
        self.assertEqual(classify(0.85), "busy")
        self.assertEqual(classify(0.86), "critical")

    def test_wait_grows_with_overload(self):
        r = R(1)
        low = compute_metrics(r, 5, 100)["wait_min"]
        high = compute_metrics(r, 5, 600)["wait_min"]
        self.assertGreater(high, low)

    def test_more_buses_less_wait(self):
        r = R(1)
        self.assertLess(compute_metrics(r, 8, 300)["wait_min"], compute_metrics(r, 4, 300)["wait_min"])

    def test_left_behind(self):
        m = compute_metrics(R(1), 1, 10_000)
        self.assertGreater(m["left_behind"], 0)

    def test_summary_empty_demand(self):
        s = system_summary([R(1)], {1: 3}, {1: 0})
        self.assertEqual(s["avg_wait_min"], 0.0)


class RecommendTests(unittest.TestCase):
    def setUp(self):
        self.busy, self.idle = R(1, "A"), R(2, "B")
        self.routes = [self.busy, self.idle]

    def test_moves_bus_from_idle_to_busy(self):
        recs = recommend(self.routes, {1: 3, 2: 6}, {1: 400, 2: 60})
        self.assertTrue(recs)
        self.assertEqual(recs[0]["from_route_id"], 2)
        self.assertEqual(recs[0]["to_route_id"], 1)
        self.assertIn("A", recs[0]["reason"])

    def test_reduces_system_wait(self):
        buses, demand = {1: 3, 2: 6}, {1: 400, 2: 60}
        before = system_summary(self.routes, buses, demand)["avg_wait_min"]
        for rec in recommend(self.routes, buses, demand):
            buses[rec["from_route_id"]] -= 1
            buses[rec["to_route_id"]] += 1
        self.assertLess(system_summary(self.routes, buses, demand)["avg_wait_min"], before)

    def test_respects_min_buses(self):
        recs = recommend(self.routes, {1: 3, 2: 2}, {1: 400, 2: 10})
        self.assertEqual(recs, [])

    def test_no_receiver_no_recs(self):
        self.assertEqual(recommend(self.routes, {1: 6, 2: 6}, {1: 100, 2: 100}), [])

    def test_no_donor_no_recs(self):
        # hər iki xətt yüklüdür -> verəcək xətt yoxdur
        self.assertEqual(recommend(self.routes, {1: 3, 2: 3}, {1: 400, 2: 400}), [])

    def test_donor_never_overloaded(self):
        buses, demand = {1: 2, 2: 10}, {1: 900, 2: 150}
        for rec in recommend(self.routes, buses, demand, max_moves=20):
            buses[rec["from_route_id"]] -= 1
            buses[rec["to_route_id"]] += 1
        self.assertLessEqual(compute_metrics(self.idle, buses[2], 150)["load_factor"], 0.70)
        self.assertGreaterEqual(buses[2], self.idle.min_buses)

    def test_bus_total_conserved(self):
        buses = {1: 3, 2: 6}
        total = sum(buses.values())
        for rec in recommend(self.routes, buses, {1: 400, 2: 60}):
            buses[rec["from_route_id"]] -= 1
            buses[rec["to_route_id"]] += 1
        self.assertEqual(sum(buses.values()), total)

    def test_max_moves_limit(self):
        recs = recommend(self.routes, {1: 1, 2: 20}, {1: 2000, 2: 10}, max_moves=2)
        self.assertLessEqual(len(recs), 2)

    def test_empty_routes(self):
        self.assertEqual(recommend([], {}, {}), [])

    def test_route_without_buses_gets_one(self):
        recs = recommend(self.routes, {1: 0, 2: 6}, {1: 100, 2: 60})
        self.assertTrue(recs and recs[0]["to_route_id"] == 1)


if __name__ == "__main__":
    unittest.main()
