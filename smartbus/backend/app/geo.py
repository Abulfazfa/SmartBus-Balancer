"""Coğrafi köməkçi funksiyalar (xətt həndəsəsi, GPS -> xəttin üzərində mövqe)."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Stop:
    id: int
    route_id: int
    seq: int
    name: str
    lat: float
    lon: float
    popularity: float = 1.0


def make_polyline(a: tuple[float, float], b: tuple[float, float], n: int, amp: float = 0.004):
    """a-dan b-yə n nöqtə; xətt düz olmasın deyə sinus əyriliyi əlavə olunur."""
    if n < 2:
        raise ValueError("Ən azı 2 dayanacaq lazımdır")
    dlat, dlon = b[0] - a[0], b[1] - a[1]
    norm = math.hypot(dlat, dlon) or 1.0
    plat, plon = -dlon / norm, dlat / norm
    pts = []
    for k in range(n):
        f = k / (n - 1)
        off = amp * math.sin(math.pi * f)
        pts.append((a[0] + dlat * f + plat * off, a[1] + dlon * f + plon * off))
    return pts


def position_at(latlons: list[tuple[float, float]], f: float) -> tuple[float, float]:
    """Xəttin f (0..1) hissəsindəki koordinat."""
    n = len(latlons)
    f = min(max(f, 0.0), 1.0)
    pos = f * (n - 1)
    k = min(int(pos), n - 2)
    t = pos - k
    (la, lo), (lb, lob) = latlons[k], latlons[k + 1]
    return la + (lb - la) * t, lo + (lob - lo) * t


def project_to_route(latlons: list[tuple[float, float]], lat: float, lon: float) -> tuple[float, float]:
    """GPS nöqtəsini xəttə proyeksiya edir -> (fraksiya 0..1, məsafə metrlə)."""
    n = len(latlons)
    if n < 2:
        raise ValueError("Xətt ən azı 2 nöqtədən ibarət olmalıdır")
    kx = 111320.0 * math.cos(math.radians(lat))
    ky = 110540.0

    def xy(p):
        return p[1] * kx, p[0] * ky

    px, py = xy((lat, lon))
    best = None
    for k in range(n - 1):
        ax, ay = xy(latlons[k])
        bx, by = xy(latlons[k + 1])
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else min(max(((px - ax) * dx + (py - ay) * dy) / l2, 0.0), 1.0)
        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if best is None or d < best[1]:
            best = ((k + t) / (n - 1), d)
    return best
