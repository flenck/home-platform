"""Database layer — persisted via asyncpg + TimescaleDB."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import asyncpg

from .const import EVENT_STATE_CHANGED

_LOGGER = logging.getLogger(__name__)

PG_DSN = os.getenv(
    "DATABASE_URL",
    "postgresql://home:home123@localhost:5432/homeplatform",
)

_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    """Initialize the connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=10)
        _LOGGER.info("Database pool created")


async def close_db() -> None:
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _ensure_pool() -> asyncpg.Pool | None:
    """Return the pool if available, without trying to reconnect."""
    return _pool


async def insert_reading(
    sensor_id: str,
    metric: str,
    value: float,
    location: str | None = None,
    unit: str | None = None,
    raw_value: float | None = None,
    metadata: dict | None = None,
) -> None:
    """Insert a sensor reading into the timeseries database."""
    pool = _ensure_pool()
    if pool is None:
        return

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


async def get_latest_readings(limit: int = 100, sensor_id: str | None = None) -> list[dict[str, Any]]:
    """Get the most recent readings, optionally filtered by sensor_id."""
    pool = _ensure_pool()
    if pool is None:
        return []

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


async def get_current_value(sensor_id: str | None = None) -> dict[str, Any] | None:
    """Get the most recent single reading."""
    pool = _ensure_pool()
    if pool is None:
        return None

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
                """,
            )
    return dict(row) if row else None


def setup_recorder(hass) -> None:
    """Listen for state change events and persist readings to the database."""

    def on_state_changed(event_type: str, data: dict) -> None:
        new_state = data.get("new_state")
        if not new_state:
            return
        entity_id = new_state.get("entity_id", "")
        if not entity_id.startswith("sensor."):
            return

        attrs = new_state.get("attributes", {})
        metric = attrs.get("metric", "")
        value = attrs.get("value")
        if value is None:
            return

        asyncio.create_task(
            insert_reading(
                sensor_id=attrs.get("sensor_id", entity_id),
                metric=metric,
                value=float(value),
                location=attrs.get("location"),
                unit=attrs.get("unit"),
                raw_value=attrs.get("raw"),
            )
        )

    hass.bus.listen(EVENT_STATE_CHANGED, on_state_changed)
    _LOGGER.info("Database recorder set up")


# ── Energy-specific queries ────────────────────────────────────────────────

_ENERGY_METRICS = [
    "daily_usage", "balance", "yearly_usage", "yearly_charge",
    "monthly_usage", "monthly_charge",
]


async def get_energy_summary() -> dict[str, Any]:
    """Get current energy summary from the latest reading of each metric."""
    pool = _ensure_pool()
    if pool is None:
        return {}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (metric) metric, value, unit, time
            FROM readings
            WHERE metric = ANY($1)
            ORDER BY metric, time DESC
            """,
            _ENERGY_METRICS,
        )

    result: dict[str, Any] = {}
    for row in rows:
        result[row["metric"]] = {
            "value": row["value"],
            "unit": row["unit"],
            "time": row["time"].isoformat(),
        }
    return result


async def get_energy_daily(days: int = 30) -> list[dict[str, Any]]:
    """Get daily electricity usage for charting."""
    pool = _ensure_pool()
    if pool is None:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT time, value
            FROM readings
            WHERE metric = 'daily_usage'
            ORDER BY time DESC
            LIMIT $1
            """,
            days,
        )
    return [{"time": row["time"].isoformat(), "value": row["value"]} for row in reversed(rows)]


async def get_energy_monthly(months: int = 12) -> list[dict[str, Any]]:
    """Get monthly electricity usage and cost history."""
    pool = _ensure_pool()
    if pool is None:
        return []

    async with pool.acquire() as conn:
        usage_rows = await conn.fetch(
            """
            SELECT time, value
            FROM readings
            WHERE metric = 'monthly_usage'
            ORDER BY time DESC
            LIMIT $1
            """,
            months,
        )
        charge_rows = await conn.fetch(
            """
            SELECT time, value
            FROM readings
            WHERE metric = 'monthly_charge'
            ORDER BY time DESC
            LIMIT $1
            """,
            months,
        )

    usage_map = {row["time"].strftime("%Y-%m"): row["value"] for row in usage_rows}
    charge_map = {row["time"].strftime("%Y-%m"): row["value"] for row in charge_rows}

    months_set: set[str] = set()
    months_set.update(usage_map.keys())
    months_set.update(charge_map.keys())

    return sorted(
        [
            {
                "month": m,
                "usage": usage_map.get(m, 0),
                "charge": charge_map.get(m, 0),
            }
            for m in months_set
        ],
        key=lambda x: x["month"],
    )
