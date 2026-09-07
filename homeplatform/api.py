"""REST API routes for the Home Platform."""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from .const import DEFAULT_API_TOKEN
from .database import (
    close_db,
    delete_baby_record,
    end_baby_sleep,
    ensure_baby_tables,
    get_baby_growth,
    get_baby_records,
    get_baby_summary,
    get_current_value,
    get_energy_daily,
    get_energy_monthly,
    get_energy_summary,
    get_latest_readings,
    init_db,
    insert_baby_record,
    setup_recorder,
    start_baby_sleep,
)

_LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api"])

# ── HA-compatible state update ──────────────────────────────────────────────

ha_router = APIRouter(tags=["ha-compat"])


@ha_router.post("/api/states/{entity_id:path}")
async def ha_state_update(
    entity_id: str,
    request: Request,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Accept Home Assistant-format state updates from external tools."""
    from .core import _hass_instance

    # Validate token
    if _hass_instance is None:
        raise HTTPException(status_code=503, detail="Platform not running")
    api_token = _hass_instance.config.get("homeplatform.api_token", DEFAULT_API_TOKEN)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.removeprefix("Bearer ") != api_token:
        raise HTTPException(status_code=403, detail="Invalid token")

    body = await request.json()
    state = body.get("state", "")
    attributes = body.get("attributes", {})

    # Bridge HA-format to homeplatform recorder format
    if "value" not in attributes:
        try:
            attributes["value"] = float(state)
        except (ValueError, TypeError):
            attributes["value"] = state
    if "metric" not in attributes:
        attributes["metric"] = _derive_metric(entity_id)
    if "unit" not in attributes:
        attributes["unit"] = attributes.get("unit_of_measurement", "")

    _hass_instance.states.set(entity_id, str(state), attributes)
    return {"entity_id": entity_id, "state": state}


def _derive_metric(entity_id: str) -> str:
    """Extract metric name from HA-format entity_id."""
    name = entity_id.removeprefix("sensor.").rsplit("_", 1)[0]
    metric_map = {
        "last_electricity_usage": "daily_usage",
        "electricity_charge_balance": "balance",
        "yearly_electricity_usage": "yearly_usage",
        "yearly_electricity_charge": "yearly_charge",
        "month_electricity_usage": "monthly_usage",
        "month_electricity_charge": "monthly_charge",
        "month_valley_usage": "valley_usage",
        "month_flat_usage": "flat_usage",
        "month_peak_usage": "peak_usage",
        "month_tip_usage": "tip_usage",
        "prepay_balance": "prepay_balance",
    }
    for key, metric in metric_map.items():
        if name.startswith(key):
            return metric
    return name


# ── Sensor readings ────────────────────────────────────────────────────────


@router.post("/readings")
async def ingest_reading(
    request: Request,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Accept sensor readings over HTTP (ESP32 direct POST).

    Expected JSON body:
        {
          "sensor_id": "esp32-tds-sensor",
          "location": "water",
          "metric": "tds",
          "value": 123.4,
          "unit": "ppm",
          "name": "TDS 水质",            # optional
          "raw_adc": 2048,              # optional
          "raw_mv": 1650,               # optional
          "chip_temp": 42.0,            # optional
        }
    The reading is written into the state machine as sensor.{sensor_id} and
    the recorder persists it to the readings table automatically.
    """
    from .core import _hass_instance

    if _hass_instance is None:
        raise HTTPException(status_code=503, detail="Platform not running")
    api_token = _hass_instance.config.get("homeplatform.api_token", DEFAULT_API_TOKEN)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if authorization.removeprefix("Bearer ") != api_token:
        raise HTTPException(status_code=403, detail="Invalid token")

    body = await request.json()
    sensor_id = str(body.get("sensor_id") or "esp32")
    metric = str(body.get("metric") or "value")
    value = body.get("value")
    if value is None:
        raise HTTPException(status_code=422, detail="'value' is required")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="'value' must be numeric")

    unit = body.get("unit", "")
    location = body.get("location", "")
    entity_id = f"sensor.{sensor_id}"

    attributes: dict[str, Any] = {
        "metric": metric,
        "value": value,
        "unit": unit,
        "name": body.get("name", sensor_id),
        "sensor_id": sensor_id,
        "location": location,
    }
    for key in ("raw", "raw_adc", "raw_mv", "chip_temp", "status_label", "status_class"):
        if key in body:
            attributes[key] = body[key]

    _hass_instance.states.set(entity_id, f"{value:.3g}", attributes)
    return {"entity_id": entity_id, "state": f"{value:.3g}", "ok": True}


@router.get("/readings/latest")
async def latest_reading(sensor_id: str | None = Query(None)) -> dict[str, Any] | None:
    """Get the most recent sensor reading."""
    return await get_current_value(sensor_id)


@router.get("/readings")
async def readings_history(
    sensor_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=10000),
) -> dict[str, Any]:
    """Get sensor reading history (chronological order)."""
    data = await get_latest_readings(limit=limit, sensor_id=sensor_id)
    return {"data": list(reversed(data))}


@router.get("/states")
async def get_states() -> list[dict[str, Any]]:
    """Get current states of all entities via the Home Platform state machine."""
    try:
        from .core import _hass_instance

        if _hass_instance is None:
            return []
        return [s.as_dict() for s in _hass_instance.states.get_all()]
    except Exception:
        return []


# ── Energy endpoints ───────────────────────────────────────────────────────


@router.get("/energy/summary")
async def energy_summary() -> dict[str, Any]:
    """Get current energy summary: balance, month usage/cost, year usage/cost."""
    return await get_energy_summary()


@router.get("/energy/daily")
async def energy_daily(days: int = Query(30, ge=1, le=365)) -> list[dict[str, Any]]:
    """Get daily electricity usage history."""
    return await get_energy_daily(days)


@router.get("/energy/monthly")
async def energy_monthly(months: int = Query(12, ge=1, le=60)) -> list[dict[str, Any]]:
    """Get monthly electricity usage and cost history."""
    return await get_energy_monthly(months)


# ── Baby endpoints ──────────────────────────────────────────────────────────

_BABY_TYPES = {"feeding", "sleep", "diaper", "growth", "temperature", "vaccination", "note"}


@router.get("/baby/summary")
async def baby_summary() -> dict[str, Any]:
    """Get today's baby care summary (feeding/sleep/diaper + latest growth/temperature)."""
    return await get_baby_summary()


@router.get("/baby/growth")
async def baby_growth() -> list[dict[str, Any]]:
    """Get all growth records for the growth chart."""
    return await get_baby_growth()


@router.get("/baby/records")
async def baby_records(
    record_type: str | None = Query(None),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """Get baby records, newest first."""
    if record_type and record_type not in _BABY_TYPES:
        raise HTTPException(status_code=422, detail="invalid record_type")
    data = await get_baby_records(record_type=record_type, days=days, limit=limit)
    return {"data": data}


@router.post("/baby/records")
async def baby_record_create(request: Request) -> dict[str, Any]:
    """Create a baby record.

    Body (all fields optional except record_type):
        {
          "record_type": "feeding|sleep|diaper|growth|temperature|vaccination|note",
          "category": "母乳/奶粉/辅食 | 湿/脏/混合 | 疫苗名称…",
          "start_time": ISO8601 | null (default now),
          "end_time": ISO8601 | null,
          "amount": number, "amount_unit": "ml|g",
          "value": number, "value_unit": "kg|cm|℃",
          "note": "text"
        }
    """
    body = await request.json()
    record_type = str(body.get("record_type") or "").strip().lower()
    if record_type not in _BABY_TYPES:
        raise HTTPException(status_code=422, detail="invalid record_type")

    def _num(key: str) -> float | None:
        v = body.get(key)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    record_id = await insert_baby_record(
        record_type=record_type,
        category=str(body.get("category") or "") or None,
        start_time=body.get("start_time"),
        end_time=body.get("end_time"),
        amount=_num("amount"),
        amount_unit=str(body.get("amount_unit") or "") or None,
        value=_num("value"),
        value_unit=str(body.get("value_unit") or "") or None,
        note=str(body.get("note") or "") or None,
    )
    if record_id is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"id": record_id, "ok": True}


@router.post("/baby/sleep/start")
async def baby_sleep_start(request: Request) -> dict[str, Any]:
    """Start a sleep session (end_time stays NULL until ended)."""
    body = await request.json()
    record_id = await start_baby_sleep(body.get("start_time"))
    if record_id is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"id": record_id, "ok": True}


@router.post("/baby/sleep/end")
async def baby_sleep_end(request: Request) -> dict[str, Any]:
    """Close the most recent open sleep session."""
    body = await request.json()
    record_id = await end_baby_sleep(body.get("end_time"))
    if record_id is None:
        raise HTTPException(status_code=404, detail="no open sleep session")
    return {"id": record_id, "ok": True}


@router.delete("/baby/records/{record_id}")
async def baby_record_delete(record_id: int) -> dict[str, Any]:
    """Delete a baby record by id."""
    if not await delete_baby_record(record_id):
        raise HTTPException(status_code=404, detail="record not found")
    return {"ok": True}


# ── App factory ────────────────────────────────────────────────────────────


def create_app(hass: Any) -> Any:
    """Create and configure the FastAPI application."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    # Store reference for API routes
    import homeplatform.core as core_module

    core_module._hass_instance = hass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await init_db()
            await ensure_baby_tables()
            setup_recorder(hass)
        except Exception:
            _LOGGER.warning("Database unavailable, running without persistence")
        await hass.start()
        yield
        await hass.stop()
        try:
            await close_db()
        except Exception:
            pass

    app = FastAPI(title="Home Platform", lifespan=lifespan)

    # HA-compatible state endpoint (before main router for path priority)
    app.include_router(ha_router)

    app.include_router(router)

    @app.middleware("http")
    async def no_cache_static(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    static_dir = os.getenv("STATIC_DIR", "frontend")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
