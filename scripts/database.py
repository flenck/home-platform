"""Database layer — persisted via asyncpg + TimescaleDB."""

import asyncio
import json
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


async def ensure_baby_tables() -> None:
    """Create baby_records table if it does not exist."""
    pool = _ensure_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS baby_records (
                id BIGSERIAL PRIMARY KEY,
                record_type TEXT NOT NULL,
                category TEXT,
                start_time TIMESTAMPTZ NOT NULL DEFAULT now(),
                end_time TIMESTAMPTZ,
                amount NUMERIC,
                amount_unit TEXT,
                value NUMERIC,
                value_unit TEXT,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_baby_records_time ON baby_records (start_time DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_baby_records_type ON baby_records (record_type, start_time DESC)"
        )
        _LOGGER.info("baby_records table ready")


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
    # 附加 sgcc 采集/登录状态（供前端显示"获取失败"提示）
    result["sgcc_status"] = _read_sgcc_status()
    return result


def _read_sgcc_status() -> dict[str, Any]:
    """读取 ~/sgcc 采集状态文件（auto_login / fetch_daily 写入）。"""
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "sgcc", "status.json")
        if not os.path.exists(p):
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sgcc_status.json")
        if not os.path.exists(p):
            return {}
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


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


# ── Baby records ────────────────────────────────────────────────────────────

_VALID_BABY_TYPES = {"feeding", "sleep", "diaper", "growth", "temperature", "vaccination", "note"}


def _parse_dt(value: Any) -> Any:
    """Parse ISO datetime string into a timezone-aware datetime, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


async def insert_baby_record(
    record_type: str,
    category: str | None = None,
    start_time: Any = None,
    end_time: Any = None,
    amount: float | None = None,
    amount_unit: str | None = None,
    value: float | None = None,
    value_unit: str | None = None,
    note: str | None = None,
) -> int | None:
    """Insert a baby record and return its id."""
    pool = _ensure_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO baby_records
                (record_type, category, start_time, end_time, amount, amount_unit, value, value_unit, note)
            VALUES ($1, $2, COALESCE($3, now()), $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            record_type,
            category or None,
            _parse_dt(start_time),
            _parse_dt(end_time),
            amount,
            amount_unit or None,
            value,
            value_unit or None,
            note or None,
        )
    return row["id"] if row else None


async def get_baby_records(
    record_type: str | None = None,
    days: int = 7,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Get baby records, newest first."""
    pool = _ensure_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if record_type:
            rows = await conn.fetch(
                """
                SELECT id, record_type, category, start_time, end_time, amount,
                       amount_unit, value, value_unit, note, created_at
                FROM baby_records
                WHERE record_type = $1 AND start_time >= now() - make_interval(days => $2)
                ORDER BY start_time DESC
                LIMIT $3
                """,
                record_type,
                days,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, record_type, category, start_time, end_time, amount,
                       amount_unit, value, value_unit, note, created_at
                FROM baby_records
                WHERE start_time >= now() - make_interval(days => $1)
                ORDER BY start_time DESC
                LIMIT $2
                """,
                days,
                limit,
            )
    result = []
    for row in rows:
        item = dict(row)
        item["start_time"] = row["start_time"].isoformat() if row["start_time"] else None
        item["end_time"] = row["end_time"].isoformat() if row["end_time"] else None
        item["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
        result.append(item)
    return result


async def delete_baby_record(record_id: int) -> bool:
    """Delete a baby record by id; returns True if deleted."""
    pool = _ensure_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM baby_records WHERE id = $1 RETURNING id",
            record_id,
        )
    return row is not None


async def start_baby_sleep(start_time: Any = None) -> int | None:
    """Start a sleep session (end_time = NULL)."""
    return await insert_baby_record("sleep", category=None, start_time=start_time, end_time=None)


async def end_baby_sleep(end_time: Any = None) -> int | None:
    """Close the most recent open sleep session."""
    pool = _ensure_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE baby_records
            SET end_time = COALESCE($1, now())
            WHERE id = (
                SELECT id FROM baby_records
                WHERE record_type = 'sleep' AND end_time IS NULL
                ORDER BY start_time DESC LIMIT 1
            )
            RETURNING id
            """,
            _parse_dt(end_time),
        )
    return row["id"] if row else None


async def get_baby_summary() -> dict[str, Any]:
    """Compute today's feeding/sleep/diaper summary plus latest growth/temperature."""
    pool = _ensure_pool()
    if pool is None:
        return {}
    async with pool.acquire() as conn:
        feeding = await conn.fetchrow(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(amount) FILTER (WHERE amount_unit = 'ml'), 0) AS total_ml,
                   COALESCE(SUM(amount) FILTER (WHERE amount_unit = 'g'), 0) AS total_g
            FROM baby_records
            WHERE record_type = 'feeding' AND start_time >= date_trunc('day', now())
            """
        )
        sleep = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (end_time - start_time)) / 3600.0), 0) AS hours
            FROM baby_records
            WHERE record_type = 'sleep' AND end_time IS NOT NULL
              AND start_time >= date_trunc('day', now())
            """
        )
        diaper = await conn.fetchrow(
            """
            SELECT COUNT(*) AS count FROM baby_records
            WHERE record_type = 'diaper' AND start_time >= date_trunc('day', now())
            """
        )
        open_sleep = await conn.fetchrow(
            """
            SELECT id, start_time FROM baby_records
            WHERE record_type = 'sleep' AND end_time IS NULL
            ORDER BY start_time DESC LIMIT 1
            """
        )
        latest_growth = await conn.fetch(
            """
            SELECT DISTINCT ON (value_unit) value, value_unit, start_time
            FROM baby_records
            WHERE record_type = 'growth'
            ORDER BY value_unit, start_time DESC
            """
        )
        latest_temp = await conn.fetchrow(
            """
            SELECT value, start_time FROM baby_records
            WHERE record_type = 'temperature'
            ORDER BY start_time DESC LIMIT 1
            """
        )

    growth: dict[str, Any] = {}
    for row in latest_growth:
        growth[row["value_unit"]] = {
            "value": float(row["value"]) if row["value"] is not None else None,
            "time": row["start_time"].isoformat(),
        }

    return {
        "feeding_count": feeding["count"],
        "feeding_ml": float(feeding["total_ml"] or 0),
        "feeding_g": float(feeding["total_g"] or 0),
        "sleep_hours": float(sleep["hours"] or 0),
        "diaper_count": diaper["count"],
        "sleep_active": bool(open_sleep),
        "sleep_active_since": open_sleep["start_time"].isoformat() if open_sleep else None,
        "growth": growth,
        "temperature": {
            "value": float(latest_temp["value"]) if latest_temp and latest_temp["value"] is not None else None,
            "time": latest_temp["start_time"].isoformat() if latest_temp else None,
        },
    }


async def get_baby_growth() -> list[dict[str, Any]]:
    """Get all growth records (weight/height/head) in chronological order."""
    pool = _ensure_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT start_time, value, value_unit, note
            FROM baby_records
            WHERE record_type = 'growth' AND value IS NOT NULL
            ORDER BY start_time ASC
            """
        )
    return [
        {
            "time": row["start_time"].isoformat(),
            "value": float(row["value"]),
            "unit": row["value_unit"],
            "note": row["note"],
        }
        for row in rows
    ]
