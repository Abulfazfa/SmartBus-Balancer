"""Smart Bus Balancer - hesablama nüvəsi.

Bu modul verilənlər bazasından və veb-çərçivədən asılı DEYİL: yalnız təmiz funksiyalardır,
ona görə asan test olunur.

Modelin əsas fərziyyələri (arxitektura qeydində də qeyd olunub):
  * Bir avtobusun dövrə müddəti = 2 * uzunluq / orta sürət + dayanma vaxtı.
  * Saatlıq tutum = avtobus sayı * (60 / dövrə müddəti) * avtobus tutumu.
  * Doluluq (load factor) = tələbat / tutum.  >1 olarsa sərnişinlər dayanacaqda qalır.
  * Orta gözləmə = interval / 2; doluluq 80%-i keçəndə evristik olaraq artır.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

# --- Parametrlər -----------------------------------------------------------
AVG_SPEED_KMH = 18.0      # şəhər daxili orta sürət (tıxac daxil)
LAYOVER_MIN = 6.0         # son dayanacaqda dayanma
WAIT_CAP_MIN = 60.0       # gözləmənin yuxarı həddi (sonsuzluqdan qaçmaq üçün)
LOAD_CAP = 9.99           # doluluğun yuxarı həddi (JSON üçün sonlu rəqəm)
RAW_WAIT_NO_BUS = 1000.0  # avtobus yoxdur -> praktik olaraq sonsuz gözləmə (yalnız müqayisə üçün)

LOAD_CRITICAL = 0.85      # bundan yuxarı -> kritik
LOAD_BUSY = 0.75          # 0.75-0.85 -> yüklü
LOAD_IDLE = 0.40          # bundan aşağı -> avtobuslar boş gedir
DONOR_MAX_LOAD_AFTER = 0.70  # donor xətt avtobus verəndən sonra bundan çox dolmamalıdır


@dataclass(frozen=True)
class RouteInfo:
    id: int
    code: str
    name: str
    length_km: float
    bus_capacity: int
    min_buses: int


class Metrics(TypedDict):
    """compute_metrics nəticəsi (JSON-a olduğu kimi çevrilir)."""
    demand: float
    capacity: float
    load_factor: float
    headway_min: float | None
    wait_min: float
    raw_wait_min: float
    left_behind: float
    status: str


def round_trip_minutes(length_km: float) -> float:
    if length_km <= 0:
        raise ValueError("Xəttin uzunluğu müsbət olmalıdır")
    return 2 * length_km / AVG_SPEED_KMH * 60 + LAYOVER_MIN


def classify(load_factor: float) -> str:
    if load_factor > LOAD_CRITICAL:
        return "critical"   # qırmızı
    if load_factor >= LOAD_BUSY:
        return "busy"       # sarı
    if load_factor < LOAD_IDLE:
        return "idle"       # boş gedir
    return "ok"             # yaşıl


def compute_metrics(route: RouteInfo, buses: int, demand: float) -> Metrics:
    """Bir xətt üçün bir saatlıq göstəricilər."""
    if buses < 0 or demand < 0:
        raise ValueError("Avtobus sayı və tələbat mənfi ola bilməz")
    rt = round_trip_minutes(route.length_km)
    capacity = buses * (60.0 / rt) * route.bus_capacity

    if capacity > 0:
        load = min(demand / capacity, LOAD_CAP)
        headway = rt / buses
        multiplier = 1.0 + max(0.0, load - 0.8) * 5.0
        raw_wait = headway / 2.0 * multiplier
    else:  # avtobus yoxdur -> sıfıra bölmə yoxdur
        load = LOAD_CAP if demand > 0 else 0.0
        headway = None
        raw_wait = RAW_WAIT_NO_BUS if demand > 0 else 0.0
    wait = min(raw_wait, WAIT_CAP_MIN)

    return {
        "demand": demand,
        "capacity": capacity,
        "load_factor": load,
        "headway_min": headway,
        "wait_min": wait,
        "raw_wait_min": raw_wait,   # həddlənməmiş; yalnız alqoritmin fayda müqayisəsi üçün
        "left_behind": max(0.0, demand - capacity),
        "status": classify(load),
    }


def system_summary(routes: list[RouteInfo], buses: dict[int, int], demand: dict[int, float]) -> dict:
    ms = [compute_metrics(r, buses.get(r.id, 0), demand.get(r.id, 0)) for r in routes]
    total = sum(m["demand"] for m in ms)
    avg_wait = sum(m["wait_min"] * m["demand"] for m in ms) / total if total > 0 else 0.0
    return {
        "total_passengers": total,
        "total_buses": sum(buses.get(r.id, 0) for r in routes),
        "avg_wait_min": avg_wait,                      # sərnişin üzrə çəkili
        "left_behind": sum(m["left_behind"] for m in ms),
        "critical_routes": sum(1 for m in ms if m["status"] == "critical"),
        "idle_routes": sum(1 for m in ms if m["status"] == "idle"),
    }


def _pick_donor(routes: list[RouteInfo], recv: RouteInfo, state: dict[int, int],
                demand: dict[int, float], metrics: dict[int, Metrics]) -> dict | None:
    """Ən çox fayda verən donor xətti seçir (və ya None)."""
    best = None
    recv_before = metrics[recv.id]
    recv_after = compute_metrics(recv, state[recv.id] + 1, demand.get(recv.id, 0))
    for d in routes:
        if d.id == recv.id:
            continue
        dm = metrics[d.id]
        if dm["load_factor"] >= LOAD_IDLE:
            continue
        if state[d.id] - 1 < d.min_buses:
            continue
        d_after = compute_metrics(d, state[d.id] - 1, demand.get(d.id, 0))
        if d_after["load_factor"] > DONOR_MAX_LOAD_AFTER:
            continue
        # həddlənməmiş gözləmə ilə: 60 dəq həddinə çatmış xətt də irəliləyişi göstərsin
        gain = (recv_before["demand"] * (recv_before["raw_wait_min"] - recv_after["raw_wait_min"])
                - dm["demand"] * (d_after["raw_wait_min"] - dm["raw_wait_min"]))
        if gain <= 0:
            continue
        if best is None or gain > best["gain"]:
            best = {"donor": d, "gain": gain, "donor_before": dm, "donor_after": d_after,
                    "recv_before": recv_before, "recv_after": recv_after}
    return best


def recommend(routes: list[RouteInfo], buses: dict[int, int], demand: dict[int, float],
              hour: int | None = None, label: str = "", max_moves: int = 10) -> list[dict]:
    """Greedy alqoritm: ən yüklü xəttə ən boş xəttdən 1 avtobus ötürür, təkrarlayır."""
    state = {r.id: buses.get(r.id, 0) for r in routes}
    recs: list[dict] = []
    for step in range(1, max_moves + 1):
        metrics = {r.id: compute_metrics(r, state[r.id], demand.get(r.id, 0)) for r in routes}
        receivers = sorted((r for r in routes if metrics[r.id]["load_factor"] > LOAD_CRITICAL),
                           key=lambda r: -metrics[r.id]["load_factor"])
        chosen = None
        for recv in receivers:
            pick = _pick_donor(routes, recv, state, demand, metrics)
            if pick:
                chosen = (recv, pick)
                break
        if chosen is None:
            break
        recv, p = chosen
        d = p["donor"]
        rb, ra, db, da = p["recv_before"], p["recv_after"], p["donor_before"], p["donor_after"]
        when = f"{hour:02d}:00{label} saatında " if hour is not None else ""
        reason = (
            f"{when}{recv.code} xətti {rb['load_factor']:.0%} doludur "
            f"(orta gözləmə ≈ {rb['wait_min']:.1f} dəq), {d.code} xətti isə yalnız "
            f"{db['load_factor']:.0%} doludur - avtobusları əsasən boş gedir. "
            f"{d.code}-dən 1 avtobus {recv.code}-ə yönləndirildikdə {recv.code} üzrə gözləmə "
            f"{rb['wait_min']:.1f} → {ra['wait_min']:.1f} dəq, doluluq "
            f"{rb['load_factor']:.0%} → {ra['load_factor']:.0%} olur; "
            f"{d.code} üzrə doluluq {da['load_factor']:.0%}-ə qalxır (norma daxilində)."
        )
        recs.append({
            "step": step,
            "from_route_id": d.id, "from_code": d.code,
            "to_route_id": recv.id, "to_code": recv.code,
            "buses": 1,
            "reason": reason,
            "impact": {
                "to_wait_before": rb["wait_min"], "to_wait_after": ra["wait_min"],
                "from_wait_before": db["wait_min"], "from_wait_after": da["wait_min"],
                "to_load_before": rb["load_factor"], "to_load_after": ra["load_factor"],
                "from_load_before": db["load_factor"], "from_load_after": da["load_factor"],
                "passenger_minutes_saved": p["gain"],
            },
        })
        state[d.id] -= 1
        state[recv.id] += 1
    return recs


def suggest_donor(routes: list[RouteInfo], buses: dict[int, int], demand: dict[int, float],
                  recv_id: int) -> dict | None:
    """Verilmiş xətt üçün ən yaxşı donor xətti (alıcı həddi yoxlanmadan). Yoxdursa None."""
    state = {r.id: buses.get(r.id, 0) for r in routes}
    recv = next((r for r in routes if r.id == recv_id), None)
    if recv is None:
        return None
    metrics = {r.id: compute_metrics(r, state[r.id], demand.get(r.id, 0)) for r in routes}
    p = _pick_donor(routes, recv, state, demand, metrics)
    if not p:
        return None
    d = p["donor"]
    return {"from_route_id": d.id, "from_code": d.code, "to_route_id": recv.id,
            "donor_load_before": p["donor_before"]["load_factor"],
            "donor_load_after": p["donor_after"]["load_factor"], "gain": p["gain"]}
