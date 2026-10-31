"""FastAPI girişi. İşə salma: uvicorn app.main:app --reload --port 8000

Bu qatda biznes məntiqi yoxdur: marşrut, validasiya, xətanın HTTP koduna çevrilməsi, CORS,
isteğe bağlı API açarı və simulyasiya saatı. Bütün qaydalar service.py / live_service.py-dadır.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import service
from .db import Repository
from .errors import describe_validation_errors, validation_detail
from .live_service import LiveManager
from .simulator import seed_database


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartbus")

DB_PATH = os.environ.get("SMARTBUS_DB", str(Path(__file__).resolve().parents[1] / "smartbus.db"))
repo = Repository(DB_PATH)
live = LiveManager(repo)

CLOCK_PERIOD_S = 1.0       # saat neçə real saniyədən bir işləyir
MAX_CLOCK_STEP_S = 5.0     # kompüter yuxuya getsə, simulyasiya "partlamasın"

HourQuery = Annotated[int, Query(ge=service.MIN_HOUR, le=service.MAX_HOUR, description="Saat (6-23)")]
BasisQuery = Annotated[str, Query(pattern="^(current|forecast)$", description="current | forecast")]
DirectionQuery = Annotated[str, Query(pattern="^(forward|backward)$")]


async def _clock():
    """Canlı simulyasiya saatı: real keçən vaxt x sürət qədər simulyasiya saniyəsi irəli aparır."""
    last = time.monotonic()
    while True:
        await asyncio.sleep(CLOCK_PERIOD_S)
        now = time.monotonic()
        elapsed, last = min(now - last, MAX_CLOCK_STEP_S), now
        if live.paused:
            continue
        try:
            await asyncio.to_thread(live.advance, elapsed * live.speed)
        except Exception:  # saat dayanmasın
            log.exception("Simulyasiya addımı uğursuz oldu")


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_database(repo)  # baza boşdursa simulyasiya datası ilə doldurur
    live.rebuild()
    log.info("Smart Bus Balancer işə düşdü (baza: %s)", DB_PATH)
    task = asyncio.create_task(_clock())
    try:
        yield
    finally:
        task.cancel()


# TƏK VƏ ƏSAS APP OBYEKTİ
app = FastAPI(title="Smart Bus Balancer API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ===================== Statik Fayllar Və Frontend =====================
static_dir = os.path.join(os.path.dirname(__file__), "static")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Frontend index.html faylı tapılmadı."}


# ===================== Xəta modeli: hamısı {"detail": mətn} =====================
@app.exception_handler(service.NotFoundError)
async def _not_found(_: Request, exc: service.NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(service.ServiceError)
async def _rule_violation(_: Request, exc: service.ServiceError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
async def _invalid_request(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": validation_detail(exc.errors()),
            "errors": describe_validation_errors(exc.errors()),
        },
    )


# ===================== İsteğe bağlı API açarı =====================
def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """SMARTBUS_API_KEY təyin olunubsa, dəyişdirən endpointlər X-API-Key başlığı tələb edir.

    Təyin olunmayıbsa (demo/yarış rejimi) açıqdır. Açar hər sorğuda oxunur ki, yenidən başlatmadan dəyişsin.
    """
    expected = os.environ.get("SMARTBUS_API_KEY")
    if expected and not secrets.compare_digest((x_api_key or "").encode(), expected.encode()):
        raise HTTPException(401, "API açarı yanlışdır və ya göndərilməyib (X-API-Key)")


WRITE = [Depends(require_api_key)]


# ===================== Modellər =====================
class MoveRequest(BaseModel):
    from_route_id: int
    to_route_id: int
    buses: int = Field(1, ge=1, le=10)
    hour: int = Field(8, ge=service.MIN_HOUR, le=service.MAX_HOUR)
    reason: str | None = None


class SimControl(BaseModel):
    speed: float | None = Field(None, gt=0, le=600)
    paused: bool | None = None
    time: str | None = Field(None, description="SS:DD, məsələn 07:30")


class StepRequest(BaseModel):
    minutes: float = Field(..., gt=0, le=120)


class IncidentRequest(BaseModel):
    bus_id: int
    minutes: float = Field(10, gt=0, le=120)


class TelemetryRequest(BaseModel):
    bus_id: int
    occupancy: float | None = Field(None, ge=0, description="Avtobusdakı sərnişin sayı (sayğac)")
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)


# ===================== Sistem =====================
@app.get("/api/health")
def health():
    return {"status": "ok"}


# ===================== Balanslaşdırma (saatlıq analiz) =====================
@app.get("/api/routes")
def routes(hour: HourQuery = 8):
    return service.get_routes_status(repo, hour)


@app.get("/api/routes/{route_id}/profile")
def route_profile(route_id: int):
    return service.get_route_profile(repo, route_id)


@app.get("/api/recommendations")
def recommendations(hour: HourQuery = 8, basis: BasisQuery = "current"):
    return service.get_recommendations(repo, hour, basis)


@app.post("/api/recommendations/apply", dependencies=WRITE)
def apply(req: MoveRequest):
    return live.apply_move(req.from_route_id, req.to_route_id, req.buses, req.hour, req.reason)


@app.post("/api/recommendations/apply-all", dependencies=WRITE)
def apply_all(hour: HourQuery = 8, basis: BasisQuery = "current"):
    return live.apply_all(hour, basis)


@app.get("/api/comparison")
def comparison(hour: HourQuery = 8):
    return service.get_comparison(repo, hour)


@app.get("/api/history")
def history():
    return service.get_history(repo)


@app.post("/api/reset", dependencies=WRITE)
def reset():
    return live.reset()


@app.post("/api/reseed", dependencies=WRITE)
def reseed(seed: int = 42):
    return live.reseed(seed)


# ===================== Simulyasiya idarəsi və cihaz =====================
@app.get("/api/sim/state")
def sim_state():
    return live.state()


@app.post("/api/sim/control", dependencies=WRITE)
def sim_control(req: SimControl):
    return live.control(req.speed, req.paused, req.time)


@app.post("/api/sim/step", dependencies=WRITE)
def sim_step(req: StepRequest):
    return live.step(req.minutes)


@app.post("/api/sim/incident", dependencies=WRITE)
def sim_incident(req: IncidentRequest):
    return live.incident(req.bus_id, req.minutes)


@app.post("/api/telemetry", dependencies=WRITE)
def telemetry(req: TelemetryRequest):
    return live.telemetry(req.bus_id, req.occupancy, req.lat, req.lon)


# ===================== Sərnişin modulu =====================
@app.get("/api/passenger/routes")
def passenger_routes():
    return live.passenger_routes()


@app.get("/api/passenger/routes/{route_id}/stops")
def passenger_stops(route_id: int):
    return live.route_stops(route_id)


@app.get("/api/passenger/buses")
def passenger_buses(route_id: int | None = None):
    return live.passenger_buses(route_id)


@app.get("/api/passenger/eta")
def passenger_eta(route_id: int, stop_seq: Annotated[int, Query(ge=0)], direction: DirectionQuery = "forward"):
    return live.eta(route_id, stop_seq, direction)


# ===================== Operator modulu =====================
@app.get("/api/operator/overview")
def operator_overview():
    return live.overview()


@app.get("/api/operator/heatmap")
def operator_heatmap():
    return live.heatmap()


@app.get("/api/operator/anomalies")
def operator_anomalies():
    return live.anomalies()


@app.get("/api/operator/alerts")
def operator_alerts(only_open: bool = False):
    return live.list_alerts(only_open)


@app.post("/api/operator/alerts/{alert_id}/apply", dependencies=WRITE)
def operator_alert_apply(alert_id: int):
    return live.apply_alert(alert_id)


@app.post("/api/operator/alerts/{alert_id}/dismiss", dependencies=WRITE)
def operator_alert_dismiss(alert_id: int):
    return live.dismiss_alert(alert_id)


# Frontend eyni serverdən təqdim olunur (frontend/ qovluğu varsa) - marşrutlardan SONRA qoşulur
_frontend = Path(__file__).resolve().parents[2] / "frontend"
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
