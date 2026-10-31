import os
import tempfile
import unittest

from app import service
from app.db import Repository
from app.live_service import LiveManager
from app.simulator import seed_database


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.repo = Repository(self.path)
        seed_database(self.repo)
        self.m = LiveManager(self.repo)
        self.m.rebuild(7.5 * 3600)

    def tearDown(self):
        os.remove(self.path)

    def test_stops_seeded_for_all_routes(self):
        stops = self.repo.load_stops()
        self.assertEqual(len(stops), 8)
        self.assertTrue(all(len(v) >= 6 for v in stops.values()))

    def test_old_db_gets_stops(self):
        with self.repo.conn() as c:
            c.execute("DELETE FROM stops")
        self.assertFalse(self.repo.has_stops())
        seed_database(self.repo)           # artıq seed olunub, amma dayanacaqlar bərpa olunmalıdır
        self.assertTrue(self.repo.has_stops())

    def test_state_and_fleet_matches_db(self):
        self.assertEqual(self.m.state()["time"], "07:30")
        self.assertEqual(self.m.state()["buses"], sum(self.repo.get_allocation().values()))

    def test_passenger_flow(self):
        routes = self.m.passenger_routes()
        self.assertEqual(len(routes), 8)
        rid = routes[0]["id"]
        stops = self.m.route_stops(rid)["stops"]
        res = self.m.eta(rid, 3, "forward")
        self.assertEqual(res["stop"]["seq"], 3)
        self.assertTrue(res["arrivals"])
        self.assertEqual(res["stop"]["name"], stops[3]["name"])
        self.assertIn(res["arrivals"][0]["density"], ("low", "medium", "high"))

    def test_eta_errors(self):
        with self.assertRaises(service.NotFoundError):
            self.m.eta(999, 1)
        with self.assertRaises(service.ServiceError):
            self.m.eta(1, 500)

    def test_ten_minute_alert_created_and_persisted(self):
        self.m.step(9)
        self.assertEqual(self.m.list_alerts(), [])
        self.m.step(3)
        alerts = self.m.list_alerts()
        self.assertTrue(alerts)
        a = alerts[0]
        self.assertEqual(a["status"], "active")
        self.assertIn("Əlavə avtobus lazımdır", a["message"])
        self.assertTrue(a["suggestion_text"])
        self.assertEqual(len({x["route_id"] for x in alerts}), len(alerts))   # hər xətt üçün bir dəfə

    def test_apply_alert_moves_bus_and_syncs_sim(self):
        self.m.step(12)
        a = next(x for x in self.m.list_alerts() if x["suggestion_from"])
        before = self.repo.get_allocation()
        res = self.m.apply_alert(a["id"])
        after = self.repo.get_allocation()
        self.assertEqual(after[a["suggestion_to"]], before[a["suggestion_to"]] + 1)
        self.assertEqual(res["alert"]["status"], "applied")
        self.assertEqual(len(self.m.sim.route_buses(a["suggestion_to"])), after[a["suggestion_to"]])
        with self.assertRaises(service.ServiceError):       # ikinci dəfə tətbiq olunmur
            self.m.apply_alert(a["id"])

    def test_no_duplicate_alert_right_after_apply(self):
        """Tətbiqdən sonra sayğac sıfırdan başlayır: 10 dəqiqə keçməmiş eyni xəttə yeni bildiriş yaranmamalıdır."""
        self.m.step(12)
        a = next(x for x in self.m.list_alerts() if x["suggestion_from"])
        self.m.apply_alert(a["id"])
        self.m.step(5)
        same = [x for x in self.m.list_alerts() if x["route_id"] == a["route_id"]]
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["status"], "applied")

    def test_dismiss_and_no_reraise(self):
        self.m.step(12)
        a = self.m.list_alerts()[0]
        self.m.dismiss_alert(a["id"])
        self.m.step(30)
        same = [x for x in self.m.list_alerts() if x["route_id"] == a["route_id"]]
        self.assertEqual(len(same), 1)
        with self.assertRaises(service.NotFoundError):
            self.m.dismiss_alert(9999)

    def test_alert_resolves_after_peak(self):
        self.m.step(12)
        self.assertTrue(self.m.list_alerts(only_open=True))
        self.m.sim.demand_fn = lambda rid, tod: 0.0      # tələbat kəskin düşür
        self.m.step(40)
        self.assertEqual(self.m.list_alerts(only_open=True), [])
        self.assertTrue(all(a["status"] == "resolved" for a in self.m.list_alerts()))
        self.assertTrue(all(a["resolved_tod"] for a in self.m.list_alerts()))

    def test_rebuild_clears_alerts(self):
        self.m.step(12)
        self.m.control(time="13:00")
        self.assertEqual(self.m.list_alerts(), [])

    def test_control_validation(self):
        with self.assertRaises(service.ServiceError):
            self.m.control(time="25:99")
        with self.assertRaises(service.ServiceError):
            self.m.control(time="abc")
        with self.assertRaises(service.ServiceError):
            self.m.control(speed=0)
        with self.assertRaises(service.ServiceError):
            self.m.step(0)
        self.assertEqual(self.m.control(time="18:00")["time"], "18:00")

    def test_incident_and_anomaly(self):
        bid = self.m.passenger_buses()[0]["id"]
        self.m.incident(bid, 20)
        self.m.step(5)
        kinds = {a["kind"] for a in self.m.anomalies()["anomalies"]}
        self.assertIn("stopped", kinds)
        with self.assertRaises(service.NotFoundError):
            self.m.incident(9999, 5)

    def test_telemetry(self):
        b = self.m.passenger_buses()[0]
        self.m.telemetry(b["id"], occupancy=75)
        self.assertGreaterEqual(next(x for x in self.m.passenger_buses() if x["id"] == b["id"])["occupancy_pct"], 90)
        with self.assertRaises(service.ServiceError):
            self.m.telemetry(b["id"])
        with self.assertRaises(service.NotFoundError):
            self.m.telemetry(9999, occupancy=3)

    def test_existing_apply_then_sync(self):
        service.apply_move(self.repo, 5, 1, 1, 8)
        self.m.sync_fleet()
        self.assertEqual(len(self.m.sim.route_buses(1)), self.repo.get_allocation()[1])
        service.reset(self.repo)
        self.m.sync_fleet()
        self.assertEqual(len(self.m.sim.route_buses(1)), self.repo.get_base_allocation()[1])

    def test_manager_apply_and_reset_keep_sim_in_sync(self):
        base = self.repo.get_base_allocation()
        self.m.apply_move(5, 1, 1, 8)
        self.assertEqual(len(self.m.sim.route_buses(1)), base[1] + 1)
        self.assertEqual(len(self.m.sim.route_buses(5)), base[5] - 1)
        self.m.reset()
        self.assertEqual(len(self.m.sim.route_buses(1)), base[1])
        res = self.m.apply_all(8)
        self.assertGreater(res["applied_count"], 0)
        self.assertEqual(len(self.m.sim.buses), sum(base.values()))   # ümumi say qorunur
        for rid, n in self.repo.get_allocation().items():
            self.assertEqual(len(self.m.sim.route_buses(rid)), n)

    def test_manager_apply_errors_do_not_touch_fleet(self):
        before = self.m.passenger_buses()
        with self.assertRaises(service.ServiceError):
            self.m.apply_move(1, 1, 1, 8)
        self.assertEqual(self.m.passenger_buses(), before)

    def test_reseed_is_deterministic_and_clears_alerts(self):
        self.m.step(12)
        self.assertTrue(self.m.list_alerts())
        demand_before = self.repo.load_demand()
        self.assertEqual(self.m.reseed(42)["seed"], 42)
        self.assertEqual(self.m.list_alerts(), [])
        self.assertEqual(self.repo.load_demand(), demand_before)
        self.m.reseed(7)
        self.assertNotEqual(self.repo.load_demand(), demand_before)

    def test_eta_waiting_is_whole_number(self):
        res = self.m.eta(1, 3, "forward")
        self.assertIsInstance(res["waiting_at_stop"], int)

    def test_overview_heatmap_shapes(self):
        o = self.m.overview()
        self.assertEqual(len(o["routes"]), 8)
        h = self.m.heatmap()
        self.assertTrue(all({"lat", "lon", "weight", "kind"} <= set(p) for p in h["points"]))

    def test_deterministic_start(self):
        other = LiveManager(self.repo)
        other.rebuild(7.5 * 3600)
        self.assertEqual(self.m.passenger_buses(), other.passenger_buses())


if __name__ == "__main__":
    unittest.main()
