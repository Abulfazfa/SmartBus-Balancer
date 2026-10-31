"""Canlı (real vaxt) simulyasiya: avtobus GPS mövqeyi, doluluq, ETA, sıxlıq xəritəsi,
anomaliyalar və "əlavə avtobus lazımdır" bildirişləri.

Real sistemdə avtobuslardakı GPS + sərnişin sayğacı məlumatı /api/telemetry ilə daxil olur.
Real cihaz olmadığı üçün eyni məlumatı bu simulyator istehsal edir; qalan bütün modullar
(ETA, xəritə, anomaliya, bildiriş) mənbədən asılı olmadan eyni işləyir.

Mövqe modeli: hər avtobusun "faza"sı var (0..1 = tam dövrə: 0-0.5 irəli, 0.5-1 geri).
Xətt üzrə mövqe s = 2*faza (irəli) və 2-2*faza (geri).
"""
from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from .engine import LOAD_BUSY, LOAD_IDLE, RouteInfo, round_trip_minutes
from .geo import Stop, position_at, project_to_route

TICK_S = 30.0
DAY_START, DAY_END = 6 * 3600, 24 * 3600
HIGH_ENTER, HIGH_EXIT = LOAD_BUSY, 0.70      # yüksək sıxlıq: >=75%, histerezis 70%
HIGH_WINDOW_S = 600.0                        # 10 dəqiqə
SPEED_LOAD_PENALTY = 0.15                    # dolu avtobus bir az yavaş gedir (dayanma vaxtı)
STOPPED_ALERT_S = 180.0
BUNCH_RATIO = 0.25                           # interval normanın 25%-dən azdır -> qruplaşma
GAP_RATIO = 2.0                              # interval norma x2-dən çoxdur -> boşluq
SPIKE_DELTA = 0.25                           # 10 dəqdə +25 faiz bəndi
MAX_TELEMETRY_DISTANCE_M = 500.0


def density_level(fraction: float) -> str:
    if fraction >= LOAD_BUSY:
        return "high"
    if fraction < LOAD_IDLE:
        return "low"
    return "medium"


def _s_of(phase: float) -> float:
    return 2 * phase if phase < 0.5 else 2 - 2 * phase


def _crossed(prev: float, new: float, target: float) -> bool:
    adv = (new - prev) % 1.0
    off = (target - prev) % 1.0
    return adv > 0 and 0 < off <= adv


@dataclass
class Bus:
    id: int
    plate: str
    route_id: int
    phase: float
    noise: float = 1.0
    occupancy: float = 0.0
    frozen_until: float = -1.0
    stopped_s: float = 0.0
    manual_until: float = -1.0


class LiveSimulation:
    def __init__(self, routes: list[RouteInfo], stops: dict[int, list[Stop]], allocation: dict[int, int],
                 demand_fn, start_tod: float = 7.5 * 3600, seed: int = 7):
        for r in routes:
            if len(stops.get(r.id, [])) < 2:
                raise ValueError(f"{r.code} xətti üçün ən azı 2 dayanacaq lazımdır")
        self.routes = {r.id: r for r in routes}
        self.stops = stops
        self._latlons = {rid: [(s.lat, s.lon) for s in ss] for rid, ss in stops.items()}
        self.demand_fn = demand_fn           # (route_id, tod_seconds) -> sərnişin/saat
        self.rng = random.Random(seed)
        self.t = 0.0
        self.start_tod = float(start_tod)
        self.buses: dict[int, Bus] = {}
        self._next_id = 1
        self.last_pass: dict[tuple[int, int], float] = {}
        self.high_since: dict[int, float | None] = {r.id: None for r in routes}
        self.history: dict[int, deque] = {r.id: deque() for r in routes}
        self.alerting: set[int] = set()
        for r in routes:
            for _ in range(allocation.get(r.id, 0)):
                self._new_bus(r.id, 0.0)
            self._respace(r.id)
        for r in routes:
            cyc = self._cycle_s(r.id)
            for s in stops[r.id]:
                self.last_pass[(r.id, s.seq)] = -self.rng.uniform(0, cyc / 4)
        self._update_occupancy(None)
        self._record_history()

    # --- vaxt ----------------------------------------------------------------
    @property
    def tod(self) -> float:
        return DAY_START + (self.start_tod - DAY_START + self.t) % (DAY_END - DAY_START)

    @property
    def hour(self) -> int:
        return int(self.tod // 3600)

    @staticmethod
    def format_tod(tod: float) -> str:
        return f"{int(tod // 3600):02d}:{int(tod % 3600 // 60):02d}"

    # --- köməkçilər --------------------------------------------------------------
    def _cycle_min(self, rid: int) -> float:
        return round_trip_minutes(self.routes[rid].length_km)

    def _cycle_s(self, rid: int) -> float:
        return self._cycle_min(rid) * 60

    def _new_bus(self, rid: int, phase: float) -> Bus:
        b = Bus(self._next_id, f"90-AZ-{self._next_id:03d}", rid, phase, noise=self.rng.uniform(0.9, 1.1))
        self.buses[b.id] = b
        self._next_id += 1
        return b

    def route_buses(self, rid: int) -> list[Bus]:
        return sorted((b for b in self.buses.values() if b.route_id == rid), key=lambda b: (b.phase, b.id))

    def _respace(self, rid: int):
        bs = self.route_buses(rid)
        if not bs:
            return
        base = bs[0].phase
        for i, b in enumerate(bs):
            b.phase = (base + i / len(bs)) % 1.0

    @staticmethod
    def _gaps(bs: list[Bus]) -> dict[int, float]:
        """Hər avtobusdan qarşıdakı avtobusa qədər faza məsafəsi (cəmi = 1)."""
        n = len(bs)
        if n == 0:
            return {}
        if n == 1:
            return {bs[0].id: 1.0}
        g = {b.id: (bs[(i + 1) % n].phase - b.phase) % 1.0 for i, b in enumerate(bs)}
        if sum(g.values()) < 1e-9:
            return {b.id: 1.0 / n for b in bs}
        return g

    def _speed_factor(self, b: Bus) -> float:
        if b.frozen_until > self.t:
            return 0.0
        cap = self.routes[b.route_id].bus_capacity
        return 1.0 - SPEED_LOAD_PENALTY * min(b.occupancy / cap, 1.0)

    def bus_position(self, b: Bus) -> tuple[float, float]:
        return position_at(self._latlons[b.route_id], _s_of(b.phase))

    # --- simulyasiya ---------------------------------------------------------
    def advance(self, seconds: float) -> list[dict]:
        events: list[dict] = []
        remaining = float(seconds)
        while remaining > 1e-9:
            dt = min(TICK_S, remaining)
            self._tick(dt)
            events += self._update_alerts()
            remaining -= dt
        return events

    def _tick(self, dt: float):
        t_end = self.t + dt
        for b in self.buses.values():
            cyc = self._cycle_s(b.route_id)
            frozen = b.frozen_until > self.t
            b.stopped_s = b.stopped_s + dt if frozen else 0.0
            sf = self._speed_factor(b)
            prev = b.phase
            b.phase = (b.phase + dt / cyc * sf) % 1.0
            if sf > 0:
                for s in self.stops[b.route_id]:
                    f = s.seq / (len(self.stops[b.route_id]) - 1)
                    if _crossed(prev, b.phase, f / 2) or _crossed(prev, b.phase, (1 - f / 2) % 1.0):
                        self.last_pass[(b.route_id, s.seq)] = t_end
        self.t = t_end
        self._update_occupancy(dt)
        self._record_history()

    def _lf(self, rid: int, n: int) -> float:
        r = self.routes[rid]
        cap = n * (60.0 / self._cycle_min(rid)) * r.bus_capacity
        return self.demand_fn(rid, self.tod) / cap if cap > 0 else 0.0

    def _update_occupancy(self, dt: float | None):
        for rid, r in self.routes.items():
            bs = self.route_buses(rid)
            if not bs:
                continue
            lf = self._lf(rid, len(bs))
            gaps = self._gaps(bs)
            alpha = 1.0 if dt is None else min(1.0, dt / 120.0)
            for b in bs:
                if b.manual_until > self.t:
                    continue
                target = min(1.0, lf * gaps[b.id] * len(bs) * b.noise) * r.bus_capacity
                b.occupancy += (target - b.occupancy) * alpha

    def _record_history(self):
        for rid in self.routes:
            h = self.history[rid]
            h.append((self.t, self.route_density(rid)))
            while h and h[0][0] < self.t - 1200:
                h.popleft()

    # --- sıxlıq --------------------------------------------------------------
    def bus_density(self, b: Bus) -> float:
        return min(b.occupancy / self.routes[b.route_id].bus_capacity, 1.0)

    def route_density(self, rid: int) -> float:
        bs = self.route_buses(rid)
        return sum(self.bus_density(b) for b in bs) / len(bs) if bs else 0.0

    def density_ago(self, rid: int, seconds: float) -> float | None:
        h = self.history[rid]
        if not h or h[0][0] > self.t - seconds + TICK_S:
            return None
        for ts, d in h:
            if ts >= self.t - seconds - 1e-6:
                return d
        return None

    # --- bildirişlər ---------------------------------------------------------
    def _update_alerts(self) -> list[dict]:
        events = []
        for rid, r in self.routes.items():
            dens = self.route_density(rid)
            hs = self.high_since[rid]
            if dens >= HIGH_ENTER:
                if hs is None:
                    self.high_since[rid] = hs = self.t
            elif dens < HIGH_EXIT:
                self.high_since[rid] = hs = None
            if hs is not None and self.t - hs >= HIGH_WINDOW_S - 1e-6 and rid not in self.alerting:
                self.alerting.add(rid)
                events.append({"type": "raise", "route_id": rid, "code": r.code, "density": dens,
                               "minutes": (self.t - hs) / 60.0, "tod": self.tod})
            elif hs is None and rid in self.alerting:
                self.alerting.discard(rid)
                events.append({"type": "resolve", "route_id": rid, "code": r.code, "density": dens,
                               "tod": self.tod})
        return events

    def acknowledge(self, rid: int):
        """Operator bildirişə cavab verdi (təklif tətbiq olundu): yeni epizod üçün sayğac SIFIRDAN başlayır.

        Sayğac sıfırlanmasa, histerezis zonasında (70-75%) qalan xəttə növbəti tikdə təkrar bildiriş yaranardı.
        """
        self.alerting.discard(rid)
        self.high_since[rid] = None

    # --- ETA -----------------------------------------------------------------
    def eta(self, route_id: int, seq: int, direction: str = "forward") -> list[dict]:
        if route_id not in self.routes:
            raise KeyError(route_id)
        n = len(self.stops[route_id])
        if not 0 <= seq < n:
            raise ValueError("Dayanacaq nömrəsi yanlışdır")
        if direction not in ("forward", "backward"):
            raise ValueError("direction 'forward' və ya 'backward' olmalıdır")
        f = seq / (n - 1)
        target = f / 2 if direction == "forward" else (1 - f / 2) % 1.0
        cyc_min = self._cycle_min(route_id)
        out = []
        for b in self.route_buses(route_id):
            sf = self._speed_factor(b)
            minutes = None if sf <= 0 else ((target - b.phase) % 1.0) * cyc_min / sf
            lat, lon = self.bus_position(b)
            d = self.bus_density(b)
            out.append({"bus_id": b.id, "plate": b.plate, "eta_min": minutes, "lat": lat, "lon": lon,
                        "occupancy_pct": d * 100, "density": density_level(d),
                        "status": "stopped" if sf <= 0 else "moving"})
        out.sort(key=lambda x: (x["eta_min"] is None, x["eta_min"] or 0))
        return out

    def waiting(self, rid: int, seq: int) -> float:
        s = self.stops[rid][seq]
        minutes = max(0.0, (self.t - self.last_pass[(rid, seq)]) / 60.0)
        rate = self.demand_fn(rid, self.tod) / 60.0 / len(self.stops[rid]) * s.popularity
        return min(rate * minutes, 150.0)

    # --- xəritə ----------------------------------------------------------------
    def snapshot_buses(self) -> list[dict]:
        out = []
        for b in sorted(self.buses.values(), key=lambda x: x.id):
            lat, lon = self.bus_position(b)
            d = self.bus_density(b)
            sf = self._speed_factor(b)
            out.append({"id": b.id, "plate": b.plate, "route_id": b.route_id,
                        "route_code": self.routes[b.route_id].code, "lat": lat, "lon": lon,
                        "direction": "forward" if b.phase < 0.5 else "backward",
                        "passengers": round(b.occupancy), "capacity": self.routes[b.route_id].bus_capacity,
                        "occupancy_pct": d * 100, "density": density_level(d),
                        "status": "stopped" if sf <= 0 else "moving"})
        return out

    def heatmap(self) -> list[dict]:
        pts = []
        for b in self.snapshot_buses():
            pts.append({"lat": b["lat"], "lon": b["lon"], "weight": b["occupancy_pct"] / 100,
                        "kind": "bus", "route_code": b["route_code"]})
        for rid, ss in self.stops.items():
            for s in ss:
                w = self.waiting(rid, s.seq)
                pts.append({"lat": s.lat, "lon": s.lon, "weight": min(1.0, w / 50.0), "kind": "stop",
                            "route_code": self.routes[rid].code, "waiting": round(w)})
        return pts

    def route_overview(self, rid: int) -> dict:
        bs = self.route_buses(rid)
        r = self.routes[rid]
        d = self.route_density(rid)
        hs = self.high_since[rid]
        ago = self.density_ago(rid, 600)
        return {"route_id": rid, "code": r.code, "name": r.name, "buses": len(bs),
                "passengers": round(sum(b.occupancy for b in bs)), "capacity": len(bs) * r.bus_capacity,
                "density_pct": d * 100, "density": density_level(d),
                "high_minutes": 0.0 if hs is None else (self.t - hs) / 60.0,
                "trend_pct_10min": None if ago is None else (d - ago) * 100,
                "alert_active": rid in self.alerting}

    # --- anomaliyalar --------------------------------------------------------------
    def anomalies(self) -> list[dict]:
        out = []

        def add(kind, sev, rid, msg, bus=None):
            out.append({"kind": kind, "severity": sev, "route_id": rid, "route_code": self.routes[rid].code,
                        "bus_id": bus.id if bus else None, "message": msg})

        for rid, r in self.routes.items():
            bs = self.route_buses(rid)
            n = len(bs)
            cyc = self._cycle_min(rid)
            dens = self.route_density(rid)
            gaps = self._gaps(bs)
            for i, b in enumerate(bs):
                ahead = bs[(i + 1) % n] if n > 1 else None
                if ahead and n >= 2:
                    g = gaps[b.id]
                    norm_min = cyc / n
                    if g < BUNCH_RATIO / n:
                        add("bunching", "warning", rid,
                            f"{r.code}: {b.plate} və {ahead.plate} yan-yana gedir (məsafə {g * cyc:.1f} dəq, "
                            f"norma ≈ {norm_min:.1f} dəq) - avtobuslar qruplaşıb", b)
                    elif g > GAP_RATIO / n:
                        add("gap", "critical" if g > 3.0 / n else "warning", rid,
                            f"{r.code}: {ahead.plate} ilə {b.plate} arasında interval {g * cyc:.0f} dəq "
                            f"(norma ≈ {norm_min:.0f} dəq) - sərnişinlər uzun gözləyir", b)
                d = self.bus_density(b)
                if d >= 0.95:
                    add("overload", "warning", rid, f"{r.code}: {b.plate} tam doludur ({d:.0%})", b)
                if d < 0.20 and dens >= 0.60 and b.stopped_s <= 0:
                    add("empty_run", "warning", rid,
                        f"{r.code}: {b.plate} demək olar boş gedir ({d:.0%}), xətt üzrə orta doluluq {dens:.0%}", b)
                if b.stopped_s >= STOPPED_ALERT_S:
                    add("stopped", "critical", rid,
                        f"{r.code}: {b.plate} {b.stopped_s / 60:.0f} dəqiqədir hərəkətsizdir (nasazlıq ehtimalı)", b)
            ago = self.density_ago(rid, 600)
            if ago is not None and dens - ago >= SPIKE_DELTA:
                add("spike", "warning", rid,
                    f"{r.code}: sıxlıq 10 dəqiqədə {ago:.0%} → {dens:.0%} qalxıb (qəfil artım)")
        order = {"critical": 0, "warning": 1}
        out.sort(key=lambda a: order[a["severity"]])
        return out

    # --- idarəetmə ------------------------------------------------------------
    def inject_incident(self, bus_id: int, minutes: float):
        if bus_id not in self.buses:
            raise KeyError(bus_id)
        if minutes <= 0:
            raise ValueError("Müddət müsbət olmalıdır")
        self.buses[bus_id].frozen_until = self.t + minutes * 60

    def ingest(self, bus_id: int, occupancy: float | None = None, lat: float | None = None,
               lon: float | None = None, hold_s: float = 300.0):
        """Real cihazdan (GPS + sərnişin sayğacı) gələn ölçü."""
        b = self.buses.get(bus_id)
        if b is None:
            raise KeyError(bus_id)
        if occupancy is None and lat is None and lon is None:
            raise ValueError("Ən azı doluluq və ya GPS göndərilməlidir")
        if (lat is None) != (lon is None):
            raise ValueError("lat və lon birlikdə göndərilməlidir")
        if occupancy is not None and occupancy < 0:
            raise ValueError("Sərnişin sayı mənfi ola bilməz")
        if lat is not None:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Koordinat yanlışdır")
            frac, dist = project_to_route(self._latlons[b.route_id], lat, lon)
            if dist > MAX_TELEMETRY_DISTANCE_M:
                raise ValueError(f"GPS nöqtəsi xəttdən {dist:.0f} m uzaqdır")
            b.phase = (frac / 2) if b.phase < 0.5 else (1 - frac / 2) % 1.0
        if occupancy is not None:
            b.occupancy = min(occupancy, self.routes[b.route_id].bus_capacity * 1.25)
            b.manual_until = self.t + hold_s

    def sync_fleet(self, allocation: dict[int, int]):
        """Avtobus bölgüsü dəyişəndə (tətbiq olunmuş tövsiyə) canlı parkı uyğunlaşdırır."""
        pool: list[Bus] = []
        changed = set()
        for rid in self.routes:
            have = self.route_buses(rid)
            want = allocation.get(rid, 0)
            if len(have) > want:
                for b in sorted(have, key=lambda x: x.occupancy)[: len(have) - want]:  # ən boşunu çıxar
                    pool.append(b)
                    changed.add(rid)
        for rid in self.routes:
            have = len(self.route_buses(rid))
            want = allocation.get(rid, 0)
            for _ in range(max(0, want - have)):
                if pool:
                    b = pool.pop(0)
                    b.route_id = rid
                else:
                    b = self._new_bus(rid, 0.0)
                changed.add(rid)
        for b in pool:               # ümumi say azalıbsa artıq avtobus ləğv olunur
            del self.buses[b.id]
        for rid in changed:
            self._respace(rid)
        self._update_occupancy(None)
