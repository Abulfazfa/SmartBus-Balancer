"""Realistik simulyasiya datası (real data verilmədiyi üçün).

Səhər (08:00-09:00) və axşam (18:00-19:00) pikləri var; xətlər arasında qəsdən
balanssızlıq yaradılıb: bəzi xətlər pikdə həddən artıq yüklənir, digərləri boş gedir.
Eyni seed həmişə eyni nəticəni verir.
"""
from __future__ import annotations

import math
import random

from .db import Repository
from .geo import make_polyline

FIRST_HOUR, LAST_HOUR = 6, 23

# code, name, length_km, base_buses, min_buses, səhər piki, axşam piki  (sərnişin/saat)
ROUTES = [
    ("65",  "Sumqayıt yolu - Mərkəz",        14, 6, 2, 380, 200),
    ("88",  "Yasamal - Bakıxanov",           11, 5, 2, 330, 340),
    ("125", "Əhmədli - Şəhər mərkəzi",        9, 4, 2, 270, 100),
    ("14",  "Sahil - Nərimanov",              8, 6, 2, 300, 310),
    ("2",   "Dənizkənarı - Metro",            7, 7, 2, 190, 560),
    ("131", "Xırdalan - Mərkəz",             16, 8, 2, 130, 120),
    ("5",   "Bayıl - Gənclik",                6, 6, 2, 200, 320),
    ("31",  "Binəqədi - Nizami",             10, 5, 2, 240, 360),
]


# code -> (başlanğıc (lat, lon), son (lat, lon), əyrilik). Koordinatlar təxminidir (simulyasiya).
ROUTE_GEO = {
    "65":  ((40.4300, 49.7950), (40.3790, 49.8490), 0.004),
    "88":  ((40.3950, 49.8050), (40.4180, 49.9530), -0.004),
    "125": ((40.3880, 49.9500), (40.3790, 49.8490), 0.003),
    "14":  ((40.3560, 49.8350), (40.4040, 49.8700), -0.003),
    "2":   ((40.3600, 49.8200), (40.4000, 49.8700), 0.003),
    "131": ((40.4480, 49.7550), (40.3820, 49.8520), -0.005),
    "5":   ((40.3470, 49.8290), (40.4000, 49.8510), 0.003),
    "31":  ((40.4400, 49.8200), (40.3820, 49.8640), -0.003),
}


def _stop_rows(route_id: int, code: str, name: str, length_km: float, seed: int, first_id: int) -> list[dict]:
    a, b, amp = ROUTE_GEO[code]
    n = max(6, round(length_km * 1.5))
    pts = make_polyline(a, b, n, amp)
    rng = random.Random(seed * 1000 + route_id)
    first, _, last = name.partition(" - ")
    rows = []
    for k, (la, lo) in enumerate(pts):
        nm = first if k == 0 else (last or "Son dayanacaq") if k == n - 1 else f"{code} - dayanacaq {k}"
        rows.append({"id": first_id + k, "route_id": route_id, "seq": k, "name": nm, "lat": la, "lon": lo,
                     "popularity": round(rng.uniform(0.5, 1.5), 2)})
    return rows


def ensure_stops(repo: Repository, seed: int = 42):
    """Dayanacaqlar yoxdursa yaradır (köhnə bazalar üçün də işləyir)."""
    if repo.has_stops():
        return
    next_id = 1
    with repo.conn() as c:
        for r in repo.list_routes():
            if r.code not in ROUTE_GEO:
                continue
            for row in _stop_rows(r.id, r.code, r.name, r.length_km, seed, next_id):
                repo.insert_stop(c, row)
            next_id += 100


def _gauss(x: float, mu: float, sigma: float) -> float:
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def demand_profile(hour: int, am_peak: float, pm_peak: float) -> float:
    base = 0.35 * min(am_peak, pm_peak)
    mid = hour + 0.5
    return base + (am_peak - base) * _gauss(mid, 8.5, 1.0) + (pm_peak - base) * _gauss(mid, 18.5, 1.2)


def generate_demand(am_peak: float, pm_peak: float, rng: random.Random) -> dict[int, int]:
    day_factor = rng.gauss(1.0, 0.04)
    return {h: max(0, int(round(demand_profile(h, am_peak, pm_peak) * day_factor * rng.uniform(0.94, 1.06))))
            for h in range(FIRST_HOUR, LAST_HOUR + 1)}


def seed_database(repo: Repository, seed: int = 42, days: int = 14, force: bool = False) -> bool:
    """Bazanı doldurur. Artıq doludursa və force=False isə heç nə etmir (False qaytarır)."""
    repo.init_schema()
    if repo.is_seeded():
        if not force:
            ensure_stops(repo, seed)
            return False
        repo.clear_all()
    rng = random.Random(seed)
    with repo.conn() as c:
        for i, (code, name, length, buses, min_b, am, pm) in enumerate(ROUTES, start=1):
            repo.insert_route(c, {"id": i, "code": code, "name": name, "length_km": length,
                                  "bus_capacity": 80, "base_buses": buses, "min_buses": min_b})
            for day in range(0, days + 1):  # 0 = bu gün
                for hour, pax in generate_demand(am, pm, rng).items():
                    c.execute("INSERT INTO hourly_demand (route_id, day_offset, hour, passengers) "
                              "VALUES (?,?,?,?)", (i, day, hour, pax))
    ensure_stops(repo, seed)
    return True
