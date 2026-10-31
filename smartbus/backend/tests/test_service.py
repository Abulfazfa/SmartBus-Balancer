import os
import tempfile
import unittest

from app import service
from app.db import Repository
from app.simulator import seed_database


class ServiceTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.repo = Repository(self.path)
        seed_database(self.repo)

    def tearDown(self):
        os.remove(self.path)

    def test_seed_is_idempotent_and_deterministic(self):
        self.assertFalse(seed_database(self.repo))
        other = Repository(self.path + "2")
        try:
            seed_database(other)
            self.assertEqual(self.repo.load_demand(), other.load_demand())
        finally:
            os.remove(self.path + "2")

    def test_peak_hours_busier_than_midday(self):
        peak = service.get_routes_status(self.repo, 8)["summary"]["total_passengers"]
        midday = service.get_routes_status(self.repo, 13)["summary"]["total_passengers"]
        self.assertGreater(peak, midday)

    def test_status_shape_and_forecast(self):
        s = service.get_routes_status(self.repo, 8)
        self.assertEqual(len(s["routes"]), 8)
        self.assertIsNotNone(s["routes"][0]["forecast_next"])
        self.assertIsNone(service.get_routes_status(self.repo, 23)["routes"][0]["forecast_next"])

    def test_invalid_hour(self):
        for h in (5, 24, -1):
            with self.assertRaises(service.ServiceError):
                service.get_routes_status(self.repo, h)

    def test_recommendations_improve_wait(self):
        r = service.get_recommendations(self.repo, 8)
        self.assertTrue(r["recommendations"])
        self.assertLess(r["summary_after"]["avg_wait_min"], r["summary_before"]["avg_wait_min"])

    def test_forecast_basis(self):
        r = service.get_recommendations(self.repo, 7, basis="forecast")
        self.assertEqual(r["target_hour"], 8)
        with self.assertRaises(service.ServiceError):
            service.get_recommendations(self.repo, 23, basis="forecast")
        with self.assertRaises(service.ServiceError):
            service.get_recommendations(self.repo, 8, basis="nonsense")

    def test_apply_changes_allocation_and_history(self):
        before = self.repo.get_allocation()
        res = service.apply_move(self.repo, 5, 1, 1, 8)
        after = self.repo.get_allocation()
        self.assertEqual(after[5], before[5] - 1)
        self.assertEqual(after[1], before[1] + 1)
        self.assertEqual(sum(after.values()), sum(before.values()))
        self.assertLess(res["summary_after"]["avg_wait_min"], res["summary_before"]["avg_wait_min"])
        self.assertEqual(len(service.get_history(self.repo)), 1)

    def test_apply_validation(self):
        with self.assertRaises(service.NotFoundError):
            service.apply_move(self.repo, 99, 1, 1, 8)
        with self.assertRaises(service.ServiceError):
            service.apply_move(self.repo, 1, 1, 1, 8)
        with self.assertRaises(service.ServiceError):
            service.apply_move(self.repo, 5, 1, 0, 8)
        with self.assertRaises(service.ServiceError):  # min avtobus qaydası
            service.apply_move(self.repo, 3, 1, 3, 8)
        self.assertEqual(self.repo.get_allocation(), self.repo.get_base_allocation())

    def test_move_is_atomic_when_journal_fails(self):
        """Jurnala yazma uğursuz olarsa, bölgü də dəyişməməlidir (bir tranzaksiya)."""
        before = self.repo.get_allocation()
        with self.assertRaises(Exception):
            # 999 mövcud xətt deyil -> jurnal sətri FOREIGN KEY xətası verir
            self.repo.record_move({1: before[1] + 1, 5: before[5] - 1}, 8, 999, 1, 1, "t", 1.0, 0.5)
        self.assertEqual(self.repo.get_allocation(), before)
        self.assertEqual(service.get_history(self.repo), [])

    def test_apply_all_then_comparison_and_reset(self):
        res = service.apply_all(self.repo, 8)
        self.assertGreater(res["applied_count"], 0)
        cmp_ = res["comparison"]
        self.assertLess(cmp_["after"]["avg_wait_min"], cmp_["before"]["avg_wait_min"])
        self.assertEqual(cmp_["before"]["total_buses"], cmp_["after"]["total_buses"])
        service.reset(self.repo)
        self.assertEqual(self.repo.get_allocation(), self.repo.get_base_allocation())
        self.assertEqual(service.get_history(self.repo), [])
        # reset-dən sonra təkrar tövsiyə eyni olmalıdır
        self.assertGreater(len(service.get_recommendations(self.repo, 8)["recommendations"]), 0)

    def test_second_apply_all_is_noop_when_already_balanced(self):
        service.apply_all(self.repo, 8)
        again = service.get_recommendations(self.repo, 8)
        self.assertLessEqual(len(again["recommendations"]), 1)

    def test_profile(self):
        p = service.get_route_profile(self.repo, 1)
        self.assertEqual(len(p["points"]), 18)
        with self.assertRaises(service.NotFoundError):
            service.get_route_profile(self.repo, 999)

    def test_empty_db(self):
        empty = Repository(self.path + "3")
        empty.init_schema()
        try:
            with self.assertRaises(service.ServiceError):
                service.get_routes_status(empty, 8)
        finally:
            os.remove(self.path + "3")


if __name__ == "__main__":
    unittest.main()
