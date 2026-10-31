"""SQLite qatı (quraşdırma tələb etmir)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .engine import RouteInfo
from .geo import Stop

BUSY_TIMEOUT_S = 10.0   # başqa axın yazarkən "database is locked" əvəzinə gözləmə

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    length_km REAL NOT NULL CHECK (length_km > 0),
    bus_capacity INTEGER NOT NULL CHECK (bus_capacity > 0),
    base_buses INTEGER NOT NULL CHECK (base_buses >= 0),
    min_buses INTEGER NOT NULL CHECK (min_buses >= 0)
);
CREATE TABLE IF NOT EXISTS allocation (
    route_id INTEGER PRIMARY KEY REFERENCES routes(id),
    buses INTEGER NOT NULL CHECK (buses >= 0)
);
CREATE TABLE IF NOT EXISTS hourly_demand (
    route_id INTEGER NOT NULL REFERENCES routes(id),
    day_offset INTEGER NOT NULL,          -- 0 = bu gün, 1 = dünən, ...
    hour INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    passengers INTEGER NOT NULL CHECK (passengers >= 0),
    PRIMARY KEY (route_id, day_offset, hour)
);
CREATE TABLE IF NOT EXISTS stops (
    id INTEGER PRIMARY KEY,
    route_id INTEGER NOT NULL REFERENCES routes(id),
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    popularity REAL NOT NULL DEFAULT 1.0,
    UNIQUE (route_id, seq)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id INTEGER NOT NULL REFERENCES routes(id),
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    suggestion_text TEXT,
    suggestion_from INTEGER,
    suggestion_to INTEGER,
    status TEXT NOT NULL DEFAULT 'active',   -- active | dismissed | applied | resolved
    created_tod TEXT NOT NULL,
    resolved_tod TEXT
);
CREATE TABLE IF NOT EXISTS reallocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    hour INTEGER NOT NULL,
    from_route_id INTEGER NOT NULL REFERENCES routes(id),
    to_route_id INTEGER NOT NULL REFERENCES routes(id),
    buses INTEGER NOT NULL,
    reason TEXT,
    avg_wait_before REAL,
    avg_wait_after REAL
);
"""


class Repository:
    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def init_schema(self):
        with self.conn() as c:
            c.execute("PRAGMA journal_mode = WAL")   # oxuyanlar yazanı bloklamasın (saat axını + sorğular)
            c.executescript(SCHEMA)

    def is_seeded(self) -> bool:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM routes").fetchone()[0] > 0

    def clear_all(self):
        with self.conn() as c:
            for t in ("alerts", "reallocations", "hourly_demand", "allocation", "stops", "routes"):
                c.execute(f"DELETE FROM {t}")

    # --- oxuma -------------------------------------------------------------
    def list_routes(self) -> list[RouteInfo]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM routes ORDER BY id").fetchall()
        return [RouteInfo(r["id"], r["code"], r["name"], r["length_km"], r["bus_capacity"], r["min_buses"])
                for r in rows]

    def get_allocation(self) -> dict[int, int]:
        with self.conn() as c:
            return {r["route_id"]: r["buses"] for r in c.execute("SELECT * FROM allocation")}

    def get_base_allocation(self) -> dict[int, int]:
        with self.conn() as c:
            return {r["id"]: r["base_buses"] for r in c.execute("SELECT id, base_buses FROM routes")}

    def load_demand(self) -> dict[int, dict[int, dict[int, int]]]:
        """{route_id: {day_offset: {hour: passengers}}}"""
        out: dict = {}
        with self.conn() as c:
            for r in c.execute("SELECT * FROM hourly_demand"):
                out.setdefault(r["route_id"], {}).setdefault(r["day_offset"], {})[r["hour"]] = r["passengers"]
        return out

    def list_reallocations(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """SELECT x.*, f.code AS from_code, t.code AS to_code
                   FROM reallocations x
                   JOIN routes f ON f.id = x.from_route_id
                   JOIN routes t ON t.id = x.to_route_id
                   ORDER BY x.id DESC""").fetchall()
        return [dict(r) for r in rows]

    # --- yazma -------------------------------------------------------------
    def insert_route(self, c, route: dict):
        c.execute("INSERT INTO routes (id, code, name, length_km, bus_capacity, base_buses, min_buses) "
                  "VALUES (:id, :code, :name, :length_km, :bus_capacity, :base_buses, :min_buses)", route)
        c.execute("INSERT INTO allocation (route_id, buses) VALUES (?, ?)", (route["id"], route["base_buses"]))

    def record_move(self, changes: dict[int, int], hour: int, from_id: int, to_id: int, buses: int,
                    reason: str | None, wait_before: float, wait_after: float):
        """Bölgünün dəyişməsi və jurnala yazılması BİR tranzaksiyadadır: ya ikisi də olur, ya heç biri."""
        with self.conn() as c:
            for rid, n in changes.items():
                c.execute("UPDATE allocation SET buses = ? WHERE route_id = ?", (n, rid))
            c.execute(
                "INSERT INTO reallocations (created_at, hour, from_route_id, to_route_id, buses, reason, "
                "avg_wait_before, avg_wait_after) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), hour, from_id, to_id, buses, reason,
                 wait_before, wait_after))

    def reset_allocation(self):
        with self.conn() as c:
            c.execute("UPDATE allocation SET buses = (SELECT base_buses FROM routes WHERE id = route_id)")
            c.execute("DELETE FROM reallocations")

    # --- dayanacaqlar -------------------------------------------------------
    def insert_stop(self, c, row: dict):
        c.execute("INSERT INTO stops (id, route_id, seq, name, lat, lon, popularity) "
                  "VALUES (:id, :route_id, :seq, :name, :lat, :lon, :popularity)", row)

    def has_stops(self) -> bool:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM stops").fetchone()[0] > 0

    def load_stops(self) -> dict[int, list[Stop]]:
        out: dict[int, list[Stop]] = {}
        with self.conn() as c:
            for r in c.execute("SELECT * FROM stops ORDER BY route_id, seq"):
                out.setdefault(r["route_id"], []).append(
                    Stop(r["id"], r["route_id"], r["seq"], r["name"], r["lat"], r["lon"], r["popularity"]))
        return out

    # --- bildirişlər -----------------------------------------------------------
    def add_alert(self, route_id, kind, severity, message, created_tod, suggestion_text=None,
                  suggestion_from=None, suggestion_to=None) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO alerts (route_id, kind, severity, message, suggestion_text, suggestion_from, "
                "suggestion_to, created_tod) VALUES (?,?,?,?,?,?,?,?)",
                (route_id, kind, severity, message, suggestion_text, suggestion_from, suggestion_to, created_tod))
            return cur.lastrowid

    def get_alert(self, alert_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT a.*, r.code AS route_code FROM alerts a JOIN routes r ON r.id = a.route_id "
                          "WHERE a.id = ?", (alert_id,)).fetchone()
        return dict(r) if r else None

    def list_alerts(self, only_open: bool = False) -> list[dict]:
        q = ("SELECT a.*, r.code AS route_code FROM alerts a JOIN routes r ON r.id = a.route_id "
             + ("WHERE a.status IN ('active','dismissed') " if only_open else "") + "ORDER BY a.id DESC")
        with self.conn() as c:
            return [dict(r) for r in c.execute(q)]

    def set_alert_status(self, alert_id: int, status: str, resolved_tod: str | None = None):
        with self.conn() as c:
            c.execute("UPDATE alerts SET status = ?, resolved_tod = COALESCE(?, resolved_tod) WHERE id = ?",
                      (status, resolved_tod, alert_id))

    def clear_alerts(self):
        with self.conn() as c:
            c.execute("DELETE FROM alerts")
