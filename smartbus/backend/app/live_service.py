"""Canlı simulyasiyanı DB/servis qatı ilə birləşdirən idarəçi (thread-safe)."""
from __future__ import annotations

import threading

from . import service
from .db import Repository
from .engine import suggest_donor
from .live import LiveSimulation, density_level
from .simulator import seed_database

LABELS_AZ = {"low": "Aşağı", "medium": "Orta", "high": "Yüksək"}
KIND_AZ = {"extra_bus": "Əlavə avtobus lazımdır"}


def make_demand_fn(demand_all):
    """Saatlıq tələbat cədvəlindən (bu gün) saatlar arası xətti interpolyasiya edən funksiya."""
    def fn(route_id: int, tod: float) -> float:
        today = demand_all.get(route_id, {}).get(0, {})
        if not today:
            return 0.0
        h = min(max(int(tod // 3600), service.MIN_HOUR), service.MAX_HOUR)
        frac = (tod % 3600) / 3600.0
        d0 = today.get(h, 0)
        d1 = today.get(h + 1, d0)
        return d0 + (d1 - d0) * frac
    return fn


def _hhmm_to_seconds(text: str) -> float:
    try:
        hh, mm = text.split(":")
        hh, mm = int(hh), int(mm)
    except Exception:
        raise service.ServiceError("Vaxt 'SS:DD' formatında olmalıdır")
    if not (service.MIN_HOUR <= hh <= service.MAX_HOUR and 0 <= mm < 60):
        raise service.ServiceError("Vaxt 06:00 ilə 23:59 arasında olmalıdır")
    return hh * 3600 + mm * 60


class LiveManager:
    def __init__(self, repo: Repository, start_tod: float = 7.5 * 3600):
        self.repo = repo
        self.lock = threading.RLock()
        self.start_tod = start_tod
        self.speed = 60.0          # 1 real saniyə = 60 sim saniyə (1 dəq)
        self.paused = False
        self.sim: LiveSimulation | None = None
        self.open_alerts: dict[int, int] = {}   # route_id -> alert_id (active/dismissed)

    # --- həyat dövrü -----------------------------------------------------------
    def rebuild(self, start_tod: float | None = None):
        with self.lock:
            if start_tod is not None:
                self.start_tod = start_tod
            routes = self.repo.list_routes()
            self.sim = LiveSimulation(routes, self.repo.load_stops(), self.repo.get_allocation(),
                                      make_demand_fn(self.repo.load_demand()), start_tod=self.start_tod)
            self.repo.clear_alerts()
            self.open_alerts = {}

    def _sim(self) -> LiveSimulation:
        if self.sim is None:
            raise service.ServiceError("Simulyasiya hələ başlamayıb")
        return self.sim

    def advance(self, seconds: float):
        with self.lock:
            events = self._sim().advance(seconds)
            for ev in events:
                self._handle_event(ev)

    def sync_fleet(self):
        with self.lock:
            if self.sim:
                self.sim.sync_fleet(self.repo.get_allocation())

    # --- bildirişlər -----------------------------------------------------------
    def _suggest(self, route_id: int):
        sim = self._sim()
        routes = self.repo.list_routes()
        demand = {r.id: sim.demand_fn(r.id, sim.tod) for r in routes}
        return suggest_donor(routes, self.repo.get_allocation(), demand, route_id)

    def _handle_event(self, ev: dict):
        rid = ev["route_id"]
        tod = self._sim().format_tod(ev["tod"])
        if ev["type"] == "raise":
            sug = self._suggest(rid)
            if sug:
                text = (f"Təklif: {sug['from_code']} xəttindən 1 avtobus yönləndirin "
                        f"(onun doluluğu {sug['donor_load_before']:.0%} → {sug['donor_load_after']:.0%}).")
            else:
                text = "Verə biləcək boş xətt tapılmadı - ehtiyat avtobus çıxarın."
            msg = (f"{ev['code']} xəttində sıxlıq {ev['minutes']:.0f} dəqiqədir YÜKSƏKDİR "
                   f"(orta doluluq {ev['density']:.0%}). Əlavə avtobus lazımdır.")
            aid = self.repo.add_alert(rid, "extra_bus", "critical", msg, tod, text,
                                      sug["from_route_id"] if sug else None, rid if sug else None)
            self.open_alerts[rid] = aid
        elif ev["type"] == "resolve":
            aid = self.open_alerts.pop(rid, None)
            if aid is not None:
                a = self.repo.get_alert(aid)
                if a and a["status"] in ("active", "dismissed"):
                    self.repo.set_alert_status(aid, "resolved", tod)

    def list_alerts(self, only_open: bool = False):
        with self.lock:
            return self.repo.list_alerts(only_open)

    def dismiss_alert(self, alert_id: int):
        with self.lock:
            a = self.repo.get_alert(alert_id)
            if not a:
                raise service.NotFoundError("Bildiriş tapılmadı")
            if a["status"] != "active":
                raise service.ServiceError("Yalnız aktiv bildiriş bağlanıla bilər")
            self.repo.set_alert_status(alert_id, "dismissed")
            return self.repo.get_alert(alert_id)

    def apply_alert(self, alert_id: int):
        with self.lock:
            a = self.repo.get_alert(alert_id)
            if not a:
                raise service.NotFoundError("Bildiriş tapılmadı")
            if a["status"] != "active":
                raise service.ServiceError("Bu bildiriş artıq aktiv deyil")
            if a["suggestion_from"] is None:
                raise service.ServiceError("Bu bildiriş üçün avtomatik təklif yoxdur")
            sim = self._sim()
            hour = min(max(sim.hour, service.MIN_HOUR), service.MAX_HOUR)
            res = service.apply_move(self.repo, a["suggestion_from"], a["suggestion_to"], 1, hour,
                                     f"Bildiriş #{alert_id}: {a['message']}")
            self.sync_fleet()
            self.repo.set_alert_status(alert_id, "applied", sim.format_tod(sim.tod))
            self.open_alerts.pop(a["route_id"], None)
            sim.acknowledge(a["route_id"])
            return {"alert": self.repo.get_alert(alert_id), "result": res}

    # --- bölgünü dəyişdirən əməliyyatlar (kilid altında + canlı parkın sinxronlaşması) --------
    def _mutate_fleet(self, fn, *args):
        """DB-də bölgünü dəyişən servis əməliyyatını simulyasiya saatı ilə yarışmadan icra edir."""
        with self.lock:
            res = fn(self.repo, *args)
            self.sync_fleet()
            return res

    def apply_move(self, from_id: int, to_id: int, buses: int, hour: int, reason: str | None = None):
        return self._mutate_fleet(service.apply_move, from_id, to_id, buses, hour, reason)

    def apply_all(self, hour: int, basis: str = "current"):
        return self._mutate_fleet(service.apply_all, hour, basis)

    def reset(self):
        return self._mutate_fleet(service.reset)

    def reseed(self, seed: int = 42):
        """Datanı yenidən yaradır və simulyasiyanı sıfırdan qurur (saat axını ilə yarışmadan)."""
        with self.lock:
            seed_database(self.repo, seed=seed, force=True)
            self.rebuild()
            return {"status": "reseeded", "seed": seed}

    # --- simulyasiya idarəsi -----------------------------------------------------
    def state(self) -> dict:
        with self.lock:
            sim = self._sim()
            return {"time": sim.format_tod(sim.tod), "hour": sim.hour, "speed": self.speed,
                    "paused": self.paused, "buses": len(sim.buses)}

    def control(self, speed=None, paused=None, time=None) -> dict:
        with self.lock:
            if speed is not None:
                if not 0 < speed <= 600:
                    raise service.ServiceError("Sürət 0 ilə 600 arasında olmalıdır")
                self.speed = float(speed)
            if paused is not None:
                self.paused = bool(paused)
            if time is not None:
                self.rebuild(_hhmm_to_seconds(time))
            return self.state()

    def step(self, minutes: float) -> dict:
        if not 0 < minutes <= 120:
            raise service.ServiceError("Addım 0 ilə 120 dəqiqə arasında olmalıdır")
        self.advance(minutes * 60)
        return self.state()

    def incident(self, bus_id: int, minutes: float):
        with self.lock:
            try:
                self._sim().inject_incident(bus_id, minutes)
            except KeyError:
                raise service.NotFoundError("Avtobus tapılmadı")
            except ValueError as e:
                raise service.ServiceError(str(e))
            return {"status": "incident_started", "bus_id": bus_id, "minutes": minutes}

    def telemetry(self, bus_id: int, occupancy=None, lat=None, lon=None):
        with self.lock:
            try:
                self._sim().ingest(bus_id, occupancy, lat, lon)
            except KeyError:
                raise service.NotFoundError("Avtobus tapılmadı")
            except ValueError as e:
                raise service.ServiceError(str(e))
            return {"status": "accepted"}

    # --- sərnişin modulu -----------------------------------------------------------
    def passenger_routes(self) -> list[dict]:
        with self.lock:
            sim = self._sim()
            out = []
            for rid in sim.routes:
                o = sim.route_overview(rid)
                out.append({"id": rid, "code": o["code"], "name": o["name"], "buses": o["buses"],
                            "stops": len(sim.stops[rid]), "density": o["density"],
                            "density_label": LABELS_AZ[o["density"]], "density_pct": o["density_pct"]})
            return service.round_floats(out)

    def route_stops(self, route_id: int) -> dict:
        with self.lock:
            sim = self._sim()
            if route_id not in sim.routes:
                raise service.NotFoundError("Xətt tapılmadı")
            r = sim.routes[route_id]
            return {"route": {"id": r.id, "code": r.code, "name": r.name},
                    "stops": [{"id": s.id, "seq": s.seq, "name": s.name, "lat": s.lat, "lon": s.lon}
                              for s in sim.stops[route_id]]}

    def passenger_buses(self, route_id: int | None = None) -> list[dict]:
        with self.lock:
            buses = self._sim().snapshot_buses()
            if route_id is not None:
                buses = [b for b in buses if b["route_id"] == route_id]
            for b in buses:
                b["density_label"] = LABELS_AZ[b["density"]]
            return service.round_floats(buses)

    def eta(self, route_id: int, stop_seq: int, direction: str = "forward") -> dict:
        with self.lock:
            sim = self._sim()
            if route_id not in sim.routes:
                raise service.NotFoundError("Xətt tapılmadı")
            try:
                arrivals = sim.eta(route_id, stop_seq, direction)
            except ValueError as e:
                raise service.ServiceError(str(e))
            stop = sim.stops[route_id][stop_seq]
            for a in arrivals:
                a["density_label"] = LABELS_AZ[a["density"]]
            moving = [a for a in arrivals if a["eta_min"] is not None]
            advice = None
            if not moving:
                advice = "Hazırda bu istiqamətdə hərəkət edən avtobus yoxdur."
            elif len(moving) >= 2 and moving[0]["density"] == "high":
                alt = next((a for a in moving[1:] if a["density"] != "high"
                            and a["eta_min"] - moving[0]["eta_min"] <= 8), None)
                if alt:
                    gap = alt["eta_min"] - moving[0]["eta_min"]
                    advice = (f"Növbəti avtobus ({moving[0]['plate']}) dolu olacaq. {gap:.0f} dəq sonra gələn "
                              f"{alt['plate']} daha boşdur ({alt['density_label'].lower()} sıxlıq).")
            return service.round_floats({
                "route": {"id": route_id, "code": sim.routes[route_id].code},
                "stop": {"seq": stop.seq, "name": stop.name, "lat": stop.lat, "lon": stop.lon},
                "direction": direction, "time": sim.format_tod(sim.tod),
                "waiting_at_stop": round(sim.waiting(route_id, stop_seq)),
                "arrivals": arrivals, "advice": advice})

    # --- operator modulu -------------------------------------------------------------
    def overview(self) -> dict:
        with self.lock:
            sim = self._sim()
            routes = [sim.route_overview(rid) for rid in sim.routes]
            for o in routes:
                o["density_label"] = LABELS_AZ[o["density"]]
            return service.round_floats({"time": sim.format_tod(sim.tod), "routes": routes,
                                   "open_alerts": len(self.open_alerts),
                                   "anomalies": len(sim.anomalies())})

    def heatmap(self) -> dict:
        with self.lock:
            sim = self._sim()
            return service.round_floats({"time": sim.format_tod(sim.tod), "points": sim.heatmap()}, 4)

    def anomalies(self) -> dict:
        with self.lock:
            sim = self._sim()
            return {"time": sim.format_tod(sim.tod), "anomalies": sim.anomalies()}
