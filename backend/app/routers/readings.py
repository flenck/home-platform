from typing import Optional

from fastapi import APIRouter, Query

from app.database import get_current_value, get_latest_readings

router = APIRouter(prefix="/readings", tags=["readings"])


@router.get("/latest")
async def latest_reading(sensor_id: Optional[str] = Query(None)):
    return await get_current_value(sensor_id)


@router.get("")
async def readings_history(
    sensor_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
):
    data = await get_latest_readings(limit=limit, sensor_id=sensor_id)
    return {"data": list(reversed(data))}
