"""Biznes məntiqi qatı: DB + engine + forecast birləşdirir. FastAPI-dən asılı deyil."""
from __future__ import annotations

from .db import Repository
from .engine import RouteInfo, compute_metrics, recommend, system_summary
from .forecast import forecast_demand

MIN_HOUR, MAX_HOUR = 6, 23


class ServiceError(ValueError):
    """Yanlış sorğu (HTTP 400)."""


class NotFoundError(ServiceError):
    """Tapılmadı (HTTP 404)."""


def round_floats(o, nd=2):
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, dict):
        return {k: round_floats(v, nd) for k, v in o.items()}
    if isinstance(o, list):
        return [round_floats(v, nd) for v in o]
    return o


def _check_hour(hour: int):
    if not isinstance(hour, int) or not MIN_HOUR <= hour <= MAX_HOUR:
        raise ServiceError(f"Saat {MIN_HOUR} ilə {MAX_HOUR} arasında olmalıdır")


def _ctx(repo: Repository):
    routes = repo.list_routes()
    if not routes:
        raise ServiceError("Verilənlər bazası boşdur. Əvvəlcə seed işlədin.")
    return routes, repo.get_allocation(), repo.load_demand()


def _observed(demand_all, routes, hour) -> dict[int, int]:
    return {r.id: demand_all.get(r.id, {}).get(0, {}).get(hour, 0) for r in routes}


def _forecast_all(demand_all, routes, target_hour) -> dict[int, dict]:
    out = {}
    for r in routes:
        days = demand_all.get(r.id, {})
        history = {d: v for d, v in days.items() if d >= 1}
        out[r.id] = forecast_demand(history, days.get(0, {}), target_hour)
    return out


def _by_id(routes) -> dict[int, RouteInfo]:
    return {r.id: r for r in routes}


# --- oxuma ------------------------------------------------------------------
def get_routes_status(repo: Repository, hour: int) -> dict:
    _check_hour(hour)
    routes, alloc, demand_all = _ctx(repo)
    obs = _observed(demand_all, routes, hour)
    fc = _forecast_all(demand_all, routes, hour + 1) if hour < MAX_HOUR else {}
    items = []
    for r in routes:
        m = compute_metrics(r, alloc.get(r.id, 0), obs[r.id])
        item = {"id": r.id, "code": r.code, "name": r.name, "length_km": r.length_km,
                "buses": alloc.get(r.id, 0), "min_buses": r.min_buses, **m, "forecast_next": None}
        if r.id in fc:
            f = fc[r.id]
            fm = compute_metrics(r, alloc.get(r.id, 0), f["value"])
            item["forecast_next"] = {"hour": f["hour"], "demand": f["value"], "method": f["method"],
                                     "load_factor": fm["load_factor"], "status": fm["status"]}
        items.append(item)
    return round_floats({"hour": hour, "routes": items, "summary": system_summary(routes, alloc, obs)})


def get_recommendations(repo: Repository, hour: int, basis: str = "current") -> dict:
    _check_hour(hour)
    if basis not in ("current", "forecast"):
        raise ServiceError("basis 'current' və ya 'forecast' olmalıdır")
    routes, alloc, demand_all = _ctx(repo)
    if basis == "forecast":
        if hour >= MAX_HOUR:
            raise ServiceError("Son saat üçün proqnoz yoxdur")
        target = hour + 1
        demand = {rid: f["value"] for rid, f in _forecast_all(demand_all, routes, target).items()}
        label = " (proqnoz)"
    else:
        target = hour
        demand = _observed(demand_all, routes, hour)
        label = ""
    recs = recommend(routes, alloc, demand, hour=target, label=label)
    after = dict(alloc)
    for rec in recs:
        after[rec["from_route_id"]] -= rec["buses"]
        after[rec["to_route_id"]] += rec["buses"]
    return round_floats({"hour": hour, "target_hour": target, "basis": basis, "recommendations": recs,
                   "summary_before": system_summary(routes, alloc, demand),
                   "summary_after": system_summary(routes, after, demand)})


def get_comparison(repo: Repository, hour: int) -> dict:
    """Əvvəl (başlanğıc bölgü) / sonra (cari bölgü) müqayisəsi."""
    _check_hour(hour)
    routes, alloc, demand_all = _ctx(repo)
    base = repo.get_base_allocation()
    obs = _observed(demand_all, routes, hour)
    per_route = []
    for r in routes:
        b = compute_metrics(r, base.get(r.id, 0), obs[r.id])
        a = compute_metrics(r, alloc.get(r.id, 0), obs[r.id])
        per_route.append({"id": r.id, "code": r.code, "buses_before": base.get(r.id, 0),
                          "buses_after": alloc.get(r.id, 0), "wait_before": b["wait_min"],
                          "wait_after": a["wait_min"], "load_before": b["load_factor"],
                          "load_after": a["load_factor"]})
    return round_floats({"hour": hour, "before": system_summary(routes, base, obs),
                   "after": system_summary(routes, alloc, obs), "per_route": per_route})


def get_route_profile(repo: Repository, route_id: int) -> dict:
    routes, alloc, demand_all = _ctx(repo)
    route = _by_id(routes).get(route_id)
    if route is None:
        raise NotFoundError(f"Xətt tapılmadı: {route_id}")
    today = demand_all.get(route_id, {}).get(0, {})
    history = {d: v for d, v in demand_all.get(route_id, {}).items() if d >= 1}
    points = []
    for h in range(MIN_HOUR, MAX_HOUR + 1):
        m = compute_metrics(route, alloc.get(route_id, 0), today.get(h, 0))
        f = forecast_demand(history, today, h)
        points.append({"hour": h, "demand": today.get(h, 0), "capacity": m["capacity"],
                       "load_factor": m["load_factor"], "wait_min": m["wait_min"],
                       "forecast": f["value"]})
    return round_floats({"route": {"id": route.id, "code": route.code, "name": route.name,
                             "buses": alloc.get(route_id, 0)}, "points": points})


def get_history(repo: Repository) -> list[dict]:
    return round_floats(repo.list_reallocations())


# --- yazma ------------------------------------------------------------------
def apply_move(repo: Repository, from_id: int, to_id: int, buses: int, hour: int,
               reason: str | None = None) -> dict:
    _check_hour(hour)
    routes, alloc, demand_all = _ctx(repo)
    idx = _by_id(routes)
    if from_id not in idx or to_id not in idx:
        raise NotFoundError("Xətt tapılmadı")
    if from_id == to_id:
        raise ServiceError("Mənbə və təyinat xətti eyni ola bilməz")
    if not isinstance(buses, int) or buses < 1:
        raise ServiceError("Avtobus sayı ən azı 1 olmalıdır")
    donor = idx[from_id]
    if alloc[from_id] - buses < donor.min_buses:
        raise ServiceError(f"{donor.code} xəttində ən azı {donor.min_buses} avtobus qalmalıdır "
                           f"(hazırda {alloc[from_id]})")
    obs = _observed(demand_all, routes, hour)
    before = system_summary(routes, alloc, obs)
    new_alloc = dict(alloc)
    new_alloc[from_id] -= buses
    new_alloc[to_id] += buses
    after = system_summary(routes, new_alloc, obs)
    repo.record_move({from_id: new_alloc[from_id], to_id: new_alloc[to_id]}, hour, from_id, to_id, buses,
                     reason, before["avg_wait_min"], after["avg_wait_min"])
    return round_floats({"applied": {"from_route_id": from_id, "to_route_id": to_id, "buses": buses},
                   "summary_before": before, "summary_after": after})


def apply_all(repo: Repository, hour: int, basis: str = "current") -> dict:
    recs = get_recommendations(repo, hour, basis)["recommendations"]
    for rec in recs:
        apply_move(repo, rec["from_route_id"], rec["to_route_id"], rec["buses"], hour, rec["reason"])
    return {"applied_count": len(recs), "comparison": get_comparison(repo, hour)}


def reset(repo: Repository) -> dict:
    repo.reset_allocation()
    return {"status": "reset"}
