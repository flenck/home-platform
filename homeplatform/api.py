"""REST API routes for the Home Platform."""

import base64
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from .const import DEFAULT_API_TOKEN
from .database import (
    close_db,
    delete_baby_record,
    delete_finance_account,
    delete_finance_budget,
    delete_finance_category,
    delete_finance_transaction,
    end_baby_sleep,
    ensure_baby_tables,
    ensure_finance_tables,
    get_baby_growth,
    get_baby_records,
    get_baby_summary,
    get_current_value,
    get_energy_daily,
    get_energy_monthly,
    get_energy_summary,
    get_finance_analysis,
    get_finance_summary,
    get_finance_trend,
    get_latest_readings,
    init_db,
    insert_baby_record,
    insert_finance_account,
    insert_finance_category,
    insert_finance_transaction,
    list_finance_accounts,
    list_finance_budgets,
    list_finance_categories,
    list_finance_transactions,
    setup_recorder,
    start_baby_sleep,
    upsert_finance_budget,
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


# ── Finance (记账：账户/交易/分类/预算，参照 Firefly III) ───────────────


@router.get("/finance/analysis")
async def finance_analysis() -> dict[str, Any]:
    """本月 / 上月 / 当年 数据分析。"""
    return await get_finance_analysis()


@router.get("/finance/summary")
async def finance_summary(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
) -> dict[str, Any]:
    """Monthly income/expense/balance + category spend + budget progress."""
    return await get_finance_summary(month)


@router.get("/finance/trend")
async def finance_trend(months: int = Query(6, ge=2, le=24)) -> list[dict[str, Any]]:
    """Income/expense trend per month."""
    return await get_finance_trend(months)


@router.get("/finance/accounts")
async def finance_accounts() -> list[dict[str, Any]]:
    """List accounts with current balance."""
    return await list_finance_accounts()


@router.post("/finance/accounts")
async def finance_account_create(request: Request) -> dict[str, Any]:
    """Create an account: {name, icon?, initial_balance?}."""
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    try:
        bal = float(body.get("initial_balance") or 0)
    except (TypeError, ValueError):
        bal = 0.0
    account_id = await insert_finance_account(
        name,
        str(body.get("icon") or "") or None,
        bal,
    )
    if account_id is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"id": account_id, "ok": True}


@router.delete("/finance/accounts/{account_id}")
async def finance_account_delete(account_id: int) -> dict[str, Any]:
    """Delete an account (only if it has no transactions)."""
    if not await delete_finance_account(account_id):
        raise HTTPException(status_code=409, detail="account in use or not found")
    return {"ok": True}


@router.get("/finance/categories")
async def finance_categories() -> list[dict[str, Any]]:
    """List categories."""
    return await list_finance_categories()


@router.post("/finance/categories")
async def finance_category_create(request: Request) -> dict[str, Any]:
    """Create a category: {name, icon?}."""
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    category_id = await insert_finance_category(name, str(body.get("icon") or "") or None)
    if category_id is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"id": category_id, "ok": True}


@router.delete("/finance/categories/{category_id}")
async def finance_category_delete(category_id: int) -> dict[str, Any]:
    """Delete a category (only if unused)."""
    if not await delete_finance_category(category_id):
        raise HTTPException(status_code=409, detail="category in use or not found")
    return {"ok": True}


@router.get("/finance/transactions")
async def finance_transactions(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """List transactions, newest first."""
    data = await list_finance_transactions(month=month, limit=limit)
    return {"data": data}


@router.post("/finance/transactions")
async def finance_transaction_create(request: Request) -> dict[str, Any]:
    """Create a transaction.

    Body:
      {
        "txn_type": "expense|income|transfer",
        "amount": number (>0),
        "account_id": int,
        "target_account_id": int (required for transfer),
        "category_id": int | null,
        "note": str | null,
        "txn_date": ISO8601 | null (default now)
      }
    """
    body = await request.json()
    txn_type = str(body.get("txn_type") or "").strip().lower()
    if txn_type not in ("expense", "income", "transfer"):
        raise HTTPException(status_code=422, detail="invalid txn_type")
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid amount")
    if amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be > 0")
    try:
        account_id = int(body.get("account_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid account_id")
    target_account_id = None
    if txn_type == "transfer":
        try:
            target_account_id = int(body.get("target_account_id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="invalid target_account_id")
        if target_account_id == account_id:
            raise HTTPException(status_code=422, detail="target must differ from account")
    category_id = None
    raw_cat = body.get("category_id")
    if raw_cat not in (None, "", "null"):
        try:
            category_id = int(raw_cat)
        except (TypeError, ValueError):
            category_id = None

    txn_id = await insert_finance_transaction(
        txn_type=txn_type,
        amount=amount,
        account_id=account_id,
        target_account_id=target_account_id,
        category_id=category_id,
        note=str(body.get("note") or "") or None,
        txn_date=body.get("txn_date"),
    )
    if txn_id is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"id": txn_id, "ok": True}


@router.delete("/finance/transactions/{txn_id}")
async def finance_transaction_delete(txn_id: int) -> dict[str, Any]:
    """Delete a transaction by id."""
    if not await delete_finance_transaction(txn_id):
        raise HTTPException(status_code=404, detail="transaction not found")
    return {"ok": True}


# 小票识别：转发到独立 OCR 服务（localhost:8001，RapidOCR）
_OCR_URL = os.getenv("RECEIPT_OCR_URL", "http://127.0.0.1:8001/ocr")


@router.post("/finance/receipt")
async def finance_receipt_ocr(request: Request) -> dict[str, Any]:
    """识别小票图片并返回结构化结果（金额/日期/分类/商家）。

    Body (JSON): {"image": "<base64>"} 或 multipart form 字段 image（文件）。
    """
    ctype = request.headers.get("content-type", "")
    img_b64 = None
    if "multipart/form-data" in ctype:
        form = await request.form()
        upload = form.get("image")
        if upload is None:
            raise HTTPException(status_code=422, detail="image file required")
        data = await upload.read()
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="image too large (max 15MB)")
        img_b64 = base64.b64encode(data).decode()
    else:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        img_b64 = body.get("image")
        if not img_b64:
            raise HTTPException(status_code=422, detail="image (base64) required")

    payload = json.dumps({"image": img_b64}).encode()
    req = urllib.request.Request(
        _OCR_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        _LOGGER.warning("OCR service unavailable: %s", e)
        raise HTTPException(status_code=503, detail="识别服务不可用，请稍后重试")
    return result


@router.get("/finance/budgets")
async def finance_budgets(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
) -> list[dict[str, Any]]:
    """List budgets for a month (or all)."""
    return await list_finance_budgets(month)


@router.post("/finance/budgets")
async def finance_budget_upsert(request: Request) -> dict[str, Any]:
    """Create/update a budget: {month: YYYY-MM, category_id: int|null, amount: number}."""
    body = await request.json()
    month = str(body.get("month") or "").strip()
    if not month or len(month) != 7:
        raise HTTPException(status_code=422, detail="invalid month")
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid amount")
    if amount < 0:
        raise HTTPException(status_code=422, detail="amount must be >= 0")
    category_id = body.get("category_id")
    if category_id in (None, "", "null"):
        category_id = None
    else:
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="invalid category_id")
    budget_id = await upsert_finance_budget(month, category_id, amount)
    if budget_id is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"id": budget_id, "ok": True}


@router.delete("/finance/budgets/{budget_id}")
async def finance_budget_delete(budget_id: int) -> dict[str, Any]:
    """Delete a budget by id."""
    if not await delete_finance_budget(budget_id):
        raise HTTPException(status_code=404, detail="budget not found")
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
            await ensure_finance_tables()
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
