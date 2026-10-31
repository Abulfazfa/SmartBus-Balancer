import unittest

from app.engine import RouteInfo, round_trip_minutes
from app.geo import Stop, make_polyline, position_at, project_to_route
from app.live import HIGH_WINDOW_S, LiveSimulation, density_level


def build(buses=3, demand=0.0, length=9, n_stops=11, route_id=1):
    r = RouteInfo(route_id, "T1", "A - B", length, 80, 1)
    pts = make_polyline((40.40, 49.80), (40.40, 49.90), n_stops, 0.0)
    stops = {route_id: [Stop(k + 1, route_id, k, f"s{k}", la, lo) for k, (la, lo) in enumerate(pts)]}
    sim = LiveSimulation([r], stops, {route_id: buses}, lambda rid, tod: demand)
    return sim, r


def set_phases(sim, phases, rid=1):
    for b, p in zip(sim.route_buses(rid), phases):
        b.phase = p


class GeoTests(unittest.TestCase):
    def test_polyline_endpoints(self):
        pts = make_polyline((40.0, 49.0), (40.1, 49.2), 5)
        self.assertAlmostEqual(pts[0][0], 40.0)
        self.assertAlmostEqual(pts[-1][1], 49.2)

    def test_polyline_needs_two(self):
        with self.assertRaises(ValueError):
            make_polyline((0, 0), (1, 1), 1)

    def test_position_clamped(self):
        pts = make_polyline((40.0, 49.0), (40.1, 49.2), 5, 0)
        self.assertEqual(position_at(pts, -1), pts[0])
        self.assertEqual(position_at(pts, 2), pts[-1])

    def test_projection(self):
        pts = make_polyline((40.0, 49.0), (40.0, 49.2), 5, 0)
        frac, dist = project_to_route(pts, 40.0001, 49.1)
        self.assertAlmostEqual(frac, 0.5, places=2)
        self.assertLess(dist, 20)
        self.assertGreater(project_to_route(pts, 40.1, 49.1)[1], 5000)


class EtaTests(unittest.TestCase):
    def test_eta_matches_geometry(self):
        sim, r = build(buses=1)
        set_phases(sim, [0.1])
        rt = round_trip_minutes(r.length_km)
        eta = sim.eta(1, 5, "forward")[0]["eta_min"]       # orta dayanacaq f=0.5 -> faza 0.25
        self.assertAlmostEqual(eta, 0.15 * rt, places=6)

    def test_backward_leg(self):
        sim, r = build(buses=1)
        set_phases(sim, [0.1])
        rt = round_trip_minutes(r.length_km)
        self.assertAlmostEqual(sim.eta(1, 5, "backward")[0]["eta_min"], 0.65 * rt, places=6)

    def test_eta_decreases_with_time(self):
        sim, _ = build(buses=1)
        set_phases(sim, [0.1])
        a = sim.eta(1, 5, "forward")[0]["eta_min"]
        sim.advance(300)
        self.assertAlmostEqual(sim.eta(1, 5, "forward")[0]["eta_min"], a - 5, places=4)

    def test_sorted_and_first_is_nearest(self):
        sim, _ = build(buses=3)
        etas = [x["eta_min"] for x in sim.eta(1, 5, "forward")]
        self.assertEqual(etas, sorted(etas))

    def test_bus_actually_arrives_when_eta_says(self):
        sim, _ = build(buses=1)
        set_phases(sim, [0.1])
        eta = sim.eta(1, 5, "forward")[0]["eta_min"]
        before = sim.last_pass[(1, 5)]
        sim.advance((eta + 0.6) * 60)
        self.assertGreater(sim.last_pass[(1, 5)], before)

    def test_invalid_inputs(self):
        sim, _ = build()
        with self.assertRaises(ValueError):
            sim.eta(1, 99, "forward")
        with self.assertRaises(ValueError):
            sim.eta(1, 1, "sideways")
        with self.assertRaises(KeyError):
            sim.eta(7, 1, "forward")

    def test_no_buses_route(self):
        sim, _ = build(buses=0, demand=100)
        self.assertEqual(sim.eta(1, 2, "forward"), [])
        self.assertEqual(sim.route_density(1), 0.0)
        sim.advance(600)
        self.assertEqual(sim.anomalies(), [])

    def test_stopped_bus_has_no_eta(self):
        sim, _ = build(buses=2)
        sim.inject_incident(sim.route_buses(1)[0].id, 30)
        etas = sim.eta(1, 5, "forward")
        self.assertIsNone(etas[-1]["eta_min"])
        self.assertEqual(etas[-1]["status"], "stopped")


class DensityTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(density_level(0.2), "low")
        self.assertEqual(density_level(0.4), "medium")
        self.assertEqual(density_level(0.74), "medium")
        self.assertEqual(density_level(0.75), "high")

    def test_high_demand_is_high(self):
        sim, _ = build(buses=2, demand=5000)
        self.assertEqual(density_level(sim.route_density(1)), "high")

    def test_zero_demand_is_low(self):
        sim, _ = build(buses=2, demand=0)
        self.assertEqual(density_level(sim.route_density(1)), "low")

    def test_occupancy_never_exceeds_capacity_fraction(self):
        sim, _ = build(buses=2, demand=10 ** 6)
        sim.advance(900)
        for b in sim.snapshot_buses():
            self.assertLessEqual(b["occupancy_pct"], 100.0)

    def test_heatmap_points(self):
        sim, _ = build(buses=2, demand=300)
        pts = sim.heatmap()
        self.assertEqual(sum(1 for p in pts if p["kind"] == "bus"), 2)
        self.assertEqual(sum(1 for p in pts if p["kind"] == "stop"), 11)
        self.assertTrue(all(0 <= p["weight"] <= 1 for p in pts))

    def test_waiting_grows_until_bus_passes(self):
        sim, _ = build(buses=1, demand=600)
        set_phases(sim, [0.30])           # avtobus artıq 5-ci dayanacağı keçib; növbəti gəliş uzaqdır
        sim.last_pass[(1, 5)] = sim.t
        w0 = sim.waiting(1, 5)
        sim.advance(600)
        self.assertGreater(sim.waiting(1, 5), w0)


class AlertTests(unittest.TestCase):
    def test_alert_after_ten_minutes_only(self):
        sim, _ = build(buses=2, demand=5000)
        ev = sim.advance(HIGH_WINDOW_S - 60)
        self.assertEqual([e for e in ev if e["type"] == "raise"], [])
        ev = sim.advance(120)
        self.assertEqual(len([e for e in ev if e["type"] == "raise"]), 1)

    def test_alert_raised_once(self):
        sim, _ = build(buses=2, demand=5000)
        total = sim.advance(3600)
        self.assertEqual(len([e for e in total if e["type"] == "raise"]), 1)

    def test_acknowledge_restarts_the_ten_minute_window(self):
        sim, _ = build(buses=2, demand=5000)
        sim.advance(HIGH_WINDOW_S + 60)
        self.assertIn(1, sim.alerting)
        sim.acknowledge(1)
        self.assertNotIn(1, sim.alerting)
        self.assertIsNone(sim.high_since[1])
        self.assertEqual([e for e in sim.advance(HIGH_WINDOW_S - 120) if e["type"] == "raise"], [])
        self.assertEqual(len([e for e in sim.advance(240) if e["type"] == "raise"]), 1)

    def test_no_alert_for_medium_density(self):
        sim, _ = build(buses=4, demand=150)     # tutum ≈291/saat -> doluluq ≈50%
        self.assertEqual(sim.advance(3600), [])

    def test_resolves_when_density_drops(self):
        sim, _ = build(buses=2, demand=5000)
        sim.advance(HIGH_WINDOW_S + 60)
        sim.demand_fn = lambda rid, tod: 0.0
        ev = sim.advance(1800)
        self.assertEqual(len([e for e in ev if e["type"] == "resolve"]), 1)

    def test_short_spike_does_not_alert(self):
        sim, _ = build(buses=2, demand=5000)
        sim.advance(300)
        sim.demand_fn = lambda rid, tod: 0.0
        sim.advance(1200)
        sim.demand_fn = lambda rid, tod: 5000
        self.assertEqual([e for e in sim.advance(420) if e["type"] == "raise"], [])


class AnomalyTests(unittest.TestCase):
    def kinds(self, sim):
        return {a["kind"] for a in sim.anomalies()}

    def test_bunching(self):
        sim, _ = build(buses=3)
        set_phases(sim, [0.10, 0.12, 0.60])
        self.assertIn("bunching", self.kinds(sim))

    def test_big_gap(self):
        sim, _ = build(buses=3)
        set_phases(sim, [0.10, 0.15, 0.20])    # üçü bir yerə yığılıb, qalan dövrə boşdur
        self.assertIn("gap", self.kinds(sim))

    def test_evenly_spaced_is_clean(self):
        sim, _ = build(buses=3, demand=0)
        set_phases(sim, [0.0, 1 / 3, 2 / 3])
        self.assertEqual(sim.anomalies(), [])

    def test_stopped_after_three_minutes(self):
        sim, _ = build(buses=3, demand=0)
        sim.inject_incident(sim.route_buses(1)[0].id, 30)
        sim.advance(120)
        self.assertNotIn("stopped", self.kinds(sim))
        sim.advance(120)
        self.assertIn("stopped", self.kinds(sim))

    def test_incident_validation(self):
        sim, _ = build()
        with self.assertRaises(KeyError):
            sim.inject_incident(999, 5)
        with self.assertRaises(ValueError):
            sim.inject_incident(1, 0)

    def test_critical_sorted_first(self):
        sim, _ = build(buses=3, demand=0)
        set_phases(sim, [0.10, 0.12, 0.60])
        sim.inject_incident(sim.route_buses(1)[0].id, 30)
        sim.advance(300)
        sev = [a["severity"] for a in sim.anomalies()]
        self.assertEqual(sev, sorted(sev, key=lambda s: 0 if s == "critical" else 1))


class TelemetryTests(unittest.TestCase):
    def test_occupancy_override_and_expiry(self):
        sim, _ = build(buses=1, demand=0)
        bid = sim.route_buses(1)[0].id
        sim.ingest(bid, occupancy=70)
        sim.advance(60)
        self.assertGreater(sim.buses[bid].occupancy, 60)
        sim.advance(600)
        self.assertLess(sim.buses[bid].occupancy, 10)    # saxlama müddəti bitəndən sonra modelə qayıdır

    def test_gps_moves_bus(self):
        sim, _ = build(buses=1)
        bid = sim.route_buses(1)[0].id
        sim.ingest(bid, lat=40.4001, lon=49.85)
        lat, lon = sim.bus_position(sim.buses[bid])
        self.assertAlmostEqual(lon, 49.85, places=2)

    def test_rejects_bad_input(self):
        sim, _ = build(buses=1)
        bid = sim.route_buses(1)[0].id
        for kwargs in ({}, {"occupancy": -5}, {"lat": 40.4}, {"lat": 41.5, "lon": 49.85},
                       {"lat": 100, "lon": 49.8}):
            with self.assertRaises(ValueError):
                sim.ingest(bid, **kwargs)
        with self.assertRaises(KeyError):
            sim.ingest(999, occupancy=5)


class FleetSyncTests(unittest.TestCase):
    def two_routes(self):
        rs = [RouteInfo(1, "A", "x - y", 9, 80, 1), RouteInfo(2, "B", "x - y", 9, 80, 1)]
        pts = make_polyline((40.4, 49.8), (40.4, 49.9), 6, 0)
        stops = {i: [Stop(i * 10 + k, i, k, "s", la, lo) for k, (la, lo) in enumerate(pts)] for i in (1, 2)}
        return LiveSimulation(rs, stops, {1: 3, 2: 3}, lambda r, t: 0.0)

    def test_move_conserves_buses_and_takes_emptiest(self):
        sim = self.two_routes()
        for i, b in enumerate(sim.route_buses(2)):
            b.occupancy = [50, 5, 30][i]
        emptiest = min(sim.route_buses(2), key=lambda b: b.occupancy).id
        sim.sync_fleet({1: 4, 2: 2})
        self.assertEqual(len(sim.route_buses(1)), 4)
        self.assertEqual(len(sim.route_buses(2)), 2)
        self.assertEqual(len(sim.buses), 6)
        self.assertEqual(sim.buses[emptiest].route_id, 1)

    def test_even_spacing_after_sync(self):
        sim = self.two_routes()
        sim.sync_fleet({1: 4, 2: 2})
        gaps = sim._gaps(sim.route_buses(1))
        for g in gaps.values():
            self.assertAlmostEqual(g, 0.25, places=6)

    def test_add_and_remove_total(self):
        sim = self.two_routes()
        sim.sync_fleet({1: 5, 2: 3})
        self.assertEqual(len(sim.buses), 8)
        sim.sync_fleet({1: 1, 2: 1})
        self.assertEqual(len(sim.buses), 2)

    def test_density_drops_after_adding_bus(self):
        r = RouteInfo(1, "A", "x - y", 9, 80, 1)
        pts = make_polyline((40.4, 49.8), (40.4, 49.9), 6, 0)
        stops = {1: [Stop(k, 1, k, "s", la, lo) for k, (la, lo) in enumerate(pts)]}
        sim = LiveSimulation([r], stops, {1: 3}, lambda rid, t: 190.0)
        before = sim.route_density(1)
        sim.sync_fleet({1: 5})
        sim.advance(900)
        self.assertLess(sim.route_density(1), before)


class ClockTests(unittest.TestCase):
    def test_time_wraps_inside_day(self):
        sim, _ = build()
        sim.start_tod = 23.5 * 3600
        sim.advance(3600)
        self.assertGreaterEqual(sim.tod, 6 * 3600)
        self.assertLess(sim.tod, 7 * 3600)

    def test_route_without_stops_rejected(self):
        with self.assertRaises(ValueError):
            LiveSimulation([RouteInfo(1, "A", "x", 9, 80, 1)], {}, {1: 1}, lambda r, t: 0)


if __name__ == "__main__":
    unittest.main()
