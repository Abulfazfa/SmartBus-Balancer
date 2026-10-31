"""API testləri. fastapi + httpx quraşdırılıbsa işləyir, yoxsa atlanır."""
import importlib.util
import os
import tempfile
import unittest

HAS = importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx")


@unittest.skipUnless(HAS, "fastapi/httpx quraşdırılmayıb")
class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["SMARTBUS_DB"] = cls.path
        from fastapi.testclient import TestClient
        from app.main import app
        cls.client = TestClient(app)
        cls.client.__enter__()  # lifespan (seed) işləsin

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        os.remove(cls.path)

    def setUp(self):
        self.client.post("/api/reset")

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})

    def test_routes(self):
        r = self.client.get("/api/routes?hour=8")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["routes"]), 8)

    def test_bad_hour_422(self):
        self.assertEqual(self.client.get("/api/routes?hour=3").status_code, 422)

    def test_recommend_and_apply_all(self):
        rec = self.client.get("/api/recommendations?hour=8").json()
        self.assertTrue(rec["recommendations"])
        res = self.client.post("/api/recommendations/apply-all?hour=8")
        self.assertEqual(res.status_code, 200)
        cmp_ = self.client.get("/api/comparison?hour=8").json()
        self.assertLess(cmp_["after"]["avg_wait_min"], cmp_["before"]["avg_wait_min"])

    def test_validation_error_is_consistent_and_azerbaijani(self):
        r = self.client.get("/api/routes?hour=3")
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertIsInstance(body["detail"], str)         # hər yerdə {detail: mətn}
        self.assertIn("hour", body["detail"])
        self.assertIn("ən azı 6", body["detail"])
        self.assertEqual(body["errors"][0]["field"], "hour")

    def test_not_found_and_bad_request_use_detail(self):
        r = self.client.get("/api/passenger/routes/999/stops")
        self.assertEqual(r.status_code, 404)
        self.assertIsInstance(r.json()["detail"], str)
        r = self.client.get("/api/passenger/eta?route_id=1&stop_seq=500")
        self.assertEqual(r.status_code, 400)

    def test_eta_endpoint_and_direction_validation(self):
        r = self.client.get("/api/passenger/eta?route_id=1&stop_seq=3&direction=forward")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["stop"]["seq"], 3)
        self.assertTrue(r.json()["arrivals"])
        self.assertEqual(self.client.get("/api/passenger/eta?route_id=1&stop_seq=3&direction=sideways").status_code, 422)
        self.assertEqual(self.client.get("/api/passenger/eta?route_id=1&stop_seq=-1").status_code, 422)

    def test_telemetry_errors(self):
        self.assertEqual(self.client.post("/api/telemetry", json={"bus_id": 1}).status_code, 400)
        self.assertEqual(self.client.post("/api/telemetry", json={"bus_id": 9999, "occupancy": 5}).status_code, 404)
        self.assertEqual(self.client.post("/api/telemetry", json={"bus_id": 1, "lat": 200, "lon": 49}).status_code, 422)
        self.assertEqual(self.client.post("/api/telemetry", json={"bus_id": 1, "occupancy": 30}).status_code, 200)

    def test_demo_scenario_alert_then_apply(self):
        """Demo: 07:30 -> 12 dəq -> bildiriş -> 'Tətbiq et' (saat dayandırılır ki, nəticə deterministik olsun)."""
        self.client.post("/api/sim/control", json={"paused": True, "time": "07:30"})
        try:
            self.assertEqual(self.client.post("/api/sim/step", json={"minutes": 12}).status_code, 200)
            alerts = self.client.get("/api/operator/alerts?only_open=true").json()
            self.assertTrue(alerts)
            alert = next(a for a in alerts if a["suggestion_from"])
            res = self.client.post(f"/api/operator/alerts/{alert['id']}/apply")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["alert"]["status"], "applied")
            self.assertEqual(self.client.post(f"/api/operator/alerts/{alert['id']}/apply").status_code, 400)
            self.assertEqual(self.client.post("/api/operator/alerts/99999/dismiss").status_code, 404)
        finally:
            self.client.post("/api/sim/control", json={"paused": False})

    def test_api_key_protects_write_endpoints_only_when_configured(self):
        os.environ["SMARTBUS_API_KEY"] = "test-key"
        try:
            self.assertEqual(self.client.post("/api/reset").status_code, 401)
            self.assertEqual(self.client.post("/api/reset", headers={"X-API-Key": "wrong"}).status_code, 401)
            self.assertEqual(self.client.post("/api/reset", headers={"X-API-Key": "test-key"}).status_code, 200)
            self.assertEqual(self.client.get("/api/health").status_code, 200)    # oxuma açıq qalır
        finally:
            os.environ.pop("SMARTBUS_API_KEY", None)
        self.assertEqual(self.client.post("/api/reset").status_code, 200)

    def test_apply_invalid_400_and_404(self):
        body = {"from_route_id": 1, "to_route_id": 1, "buses": 1, "hour": 8}
        self.assertEqual(self.client.post("/api/recommendations/apply", json=body).status_code, 400)
        body["to_route_id"] = 999
        self.assertEqual(self.client.post("/api/recommendations/apply", json=body).status_code, 404)


if __name__ == "__main__":
    unittest.main()
