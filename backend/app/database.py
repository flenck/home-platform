import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://home:home123@localhost:5432/homeplatform",
)

# Strip sqlalchemy+asyncpg prefix for direct asyncpg usage
PG_DSN = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _get_pool():
    return await asyncpg.create_pool(PG_DSN, min_size=2, max_size=10)


_pool_instance = None


async def _get_or_create_pool():
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = await _get_pool()
    return _pool_instance


async def insert_reading(
    sensor_id: str,
    metric: str,
    value: float,
    location: Optional[str] = None,
    unit: Optional[str] = None,
    raw_value: Optional[float] = None,
    metadata: Optional[dict] = None,
):
    pool = await _get_or_create_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO readings (time, sensor_id, location, metric, value, unit, raw_value, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            datetime.now(timezone.utc),
            sensor_id,
            location,
            metric,
            value,
            unit,
            raw_value,
            metadata,
        )


async def get_latest_readings(limit: int = 100, sensor_id: Optional[str] = None):
    pool = await _get_or_create_pool()
    async with pool.acquire() as conn:
        if sensor_id:
            rows = await conn.fetch(
                """
                SELECT time, sensor_id, location, metric, value, unit, raw_value, metadata
                FROM readings
                WHERE sensor_id = $1
                ORDER BY time DESC
                LIMIT $2
                """,
                sensor_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT time, sensor_id, location, metric, value, unit, raw_value, metadata
                FROM readings
                ORDER BY time DESC
                LIMIT $1
                """,
                limit,
            )
    return [dict(row) for row in rows]


async def get_current_value(sensor_id: Optional[str] = None):
    pool = await _get_or_create_pool()
    async with pool.acquire() as conn:
        if sensor_id:
            row = await conn.fetchrow(
                """
                SELECT time, sensor_id, location, metric, value, unit
                FROM readings
                WHERE sensor_id = $1
                ORDER BY time DESC
                LIMIT 1
                """,
                sensor_id,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT time, sensor_id, location, metric, value, unit
                FROM readings
                ORDER BY time DESC
                LIMIT 1
                """
            )
    return dict(row) if row else None
