"""Database layer — persisted via asyncpg + TimescaleDB."""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
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


# ── Finance (记账，参照 Firefly III：账户/交易/分类/预算) ──────────────

_DEFAULT_ACCOUNTS = [
    ("银行卡", "asset", "💳"),
    ("现金", "asset", "💵"),
    ("支付宝", "asset", "📱"),
    ("微信", "asset", "💬"),
]

_DEFAULT_CATEGORIES = [
    ("餐饮", "🍜"),
    ("交通", "🚌"),
    ("购物", "🛒"),
    ("医疗", "💊"),
    ("水电燃气", "💡"),
    ("住房", "🏠"),
    ("娱乐", "🎮"),
    ("教育", "📚"),
    ("人情往来", "🎁"),
    ("工资", "💰"),
    ("理财收益", "📈"),
    ("其他", "📦"),
]


async def ensure_finance_tables() -> None:
    """Create finance tables if they do not exist, seeding defaults."""
    pool = _ensure_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_accounts (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'asset',
                icon TEXT,
                initial_balance NUMERIC(12, 2) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_categories (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                icon TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_transactions (
                id BIGSERIAL PRIMARY KEY,
                txn_type TEXT NOT NULL CHECK (txn_type IN ('expense', 'income', 'transfer')),
                amount NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),
                account_id BIGINT NOT NULL REFERENCES finance_accounts(id),
                target_account_id BIGINT REFERENCES finance_accounts(id),
                category_id BIGINT REFERENCES finance_categories(id),
                note TEXT,
                txn_date TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_budgets (
                id BIGSERIAL PRIMARY KEY,
                month TEXT NOT NULL,
                category_id BIGINT REFERENCES finance_categories(id),
                amount NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (month, category_id)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fin_tx_date ON finance_transactions (txn_date DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fin_tx_type ON finance_transactions (txn_type, txn_date DESC)"
        )
        # 种子数据：默认账户与分类
        for name, typ, icon in _DEFAULT_ACCOUNTS:
            await conn.execute(
                "INSERT INTO finance_accounts (name, type, icon) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                name,
                typ,
                icon,
            )
        for name, icon in _DEFAULT_CATEGORIES:
            await conn.execute(
                "INSERT INTO finance_categories (name, icon) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                name,
                icon,
            )
        _LOGGER.info("finance tables ready")


async def list_finance_accounts() -> list[dict[str, Any]]:
    pool = _ensure_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id, a.name, a.type, a.icon, a.initial_balance,
                   COALESCE(SUM(CASE WHEN t.txn_type = 'income' AND t.account_id = a.id THEN t.amount
                                     WHEN t.txn_type = 'transfer' AND t.target_account_id = a.id THEN t.amount
                                     ELSE 0 END)
                          - SUM(CASE WHEN t.txn_type = 'expense' AND t.account_id = a.id THEN t.amount
                                     WHEN t.txn_type = 'transfer' AND t.account_id = a.id THEN t.amount
                                     ELSE 0 END), 0) AS flow_balance
            FROM finance_accounts a
            LEFT JOIN finance_transactions t ON t.account_id = a.id OR t.target_account_id = a.id
            GROUP BY a.id
            ORDER BY a.id
            """
        )
    result = []
    for row in rows:
        item = dict(row)
        item["initial_balance"] = float(row["initial_balance"] or 0)
        item["flow_balance"] = float(row["flow_balance"] or 0)
        item["balance"] = round(item["initial_balance"] + item["flow_balance"], 2)
        result.append(item)
    return result


async def insert_finance_account(name: str, icon: str | None = None, initial_balance: float = 0) -> int | None:
    pool = _ensure_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO finance_accounts (name, icon, initial_balance) VALUES ($1, $2, $3) RETURNING id",
            name.strip(),
            icon or None,
            round(float(initial_balance or 0), 2),
        )
    return row["id"] if row else None


async def delete_finance_account(account_id: int) -> bool:
    pool = _ensure_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        used = await conn.fetchval("SELECT 1 FROM finance_transactions WHERE account_id = $1 OR target_account_id = $1 LIMIT 1", account_id)
        if used:
            return False  # 有流水不能删
        row = await conn.fetchrow("DELETE FROM finance_accounts WHERE id = $1 RETURNING id", account_id)
    return row is not None


async def list_finance_categories() -> list[dict[str, Any]]:
    pool = _ensure_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, icon, created_at FROM finance_categories ORDER BY id"
        )
    result = []
    for row in rows:
        item = dict(row)
        item["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
        result.append(item)
    return result


async def insert_finance_category(name: str, icon: str | None = None) -> int | None:
    pool = _ensure_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO finance_categories (name, icon) VALUES ($1, $2) RETURNING id",
            name.strip(),
            icon or None,
        )
    return row["id"] if row else None


async def delete_finance_category(category_id: int) -> bool:
    pool = _ensure_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        used = await conn.fetchval("SELECT 1 FROM finance_transactions WHERE category_id = $1 LIMIT 1", category_id)
        if used:
            return False
        row = await conn.fetchrow("DELETE FROM finance_categories WHERE id = $1 RETURNING id", category_id)
    return row is not None


async def insert_finance_transaction(
    txn_type: str,
    amount: float,
    account_id: int,
    target_account_id: int | None = None,
    category_id: int | None = None,
    note: str | None = None,
    txn_date: Any = None,
) -> int | None:
    pool = _ensure_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO finance_transactions
                (txn_type, amount, account_id, target_account_id, category_id, note, txn_date)
            VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, now()))
            RETURNING id
            """,
            txn_type,
            round(float(amount), 2),
            account_id,
            target_account_id,
            category_id,
            note or None,
            _parse_dt(txn_date),
        )
    return row["id"] if row else None


async def list_finance_transactions(month: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    pool = _ensure_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if month:
            rows = await conn.fetch(
                """
                SELECT t.id, t.txn_type, t.amount, t.account_id, t.target_account_id, t.category_id,
                       t.note, t.txn_date,
                       a.name AS account_name, a.icon AS account_icon,
                       ta.name AS target_account_name,
                       c.name AS category_name, c.icon AS category_icon
                FROM finance_transactions t
                JOIN finance_accounts a ON a.id = t.account_id
                LEFT JOIN finance_accounts ta ON ta.id = t.target_account_id
                LEFT JOIN finance_categories c ON c.id = t.category_id
                WHERE to_char(t.txn_date AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM') = $1
                ORDER BY t.txn_date DESC, t.id DESC
                LIMIT $2
                """,
                month,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT t.id, t.txn_type, t.amount, t.account_id, t.target_account_id, t.category_id,
                       t.note, t.txn_date,
                       a.name AS account_name, a.icon AS account_icon,
                       ta.name AS target_account_name,
                       c.name AS category_name, c.icon AS category_icon
                FROM finance_transactions t
                JOIN finance_accounts a ON a.id = t.account_id
                LEFT JOIN finance_accounts ta ON ta.id = t.target_account_id
                LEFT JOIN finance_categories c ON c.id = t.category_id
                ORDER BY t.txn_date DESC, t.id DESC
                LIMIT $1
                """,
                limit,
            )
    result = []
    for row in rows:
        item = dict(row)
        item["amount"] = float(row["amount"])
        item["txn_date"] = row["txn_date"].isoformat() if row["txn_date"] else None
        result.append(item)
    return result


async def delete_finance_transaction(txn_id: int) -> bool:
    pool = _ensure_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM finance_transactions WHERE id = $1 RETURNING id", txn_id)
    return row is not None


async def get_finance_summary(month: str | None = None) -> dict[str, Any]:
    """Monthly income/expense/balance + per-category spend + budget progress."""
    pool = _ensure_pool()
    if pool is None:
        return {}
    if not month:
        month = datetime.now(timezone.utc).astimezone().strftime("%Y-%m")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              COALESCE(SUM(CASE WHEN txn_type = 'income' THEN amount ELSE 0 END), 0) AS income,
              COALESCE(SUM(CASE WHEN txn_type = 'expense' THEN amount ELSE 0 END), 0) AS expense
            FROM finance_transactions
            WHERE to_char(txn_date AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM') = $1
            """,
            month,
        )
        cats = await conn.fetch(
            """
            SELECT c.id, c.name, c.icon, COALESCE(SUM(t.amount), 0) AS spent
            FROM finance_categories c
            JOIN finance_transactions t ON t.category_id = c.id AND t.txn_type = 'expense'
            WHERE to_char(t.txn_date AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM') = $1
            GROUP BY c.id
            ORDER BY spent DESC
            """,
            month,
        )
        budgets = await conn.fetch(
            """
            SELECT b.id, b.month, b.category_id, b.amount, c.name AS category_name, c.icon AS category_icon
            FROM finance_budgets b
            LEFT JOIN finance_categories c ON c.id = b.category_id
            WHERE b.month = $1
            ORDER BY b.category_id NULLS FIRST
            """,
            month,
        )
    income = float(row["income"] or 0)
    expense = float(row["expense"] or 0)
    # 预算执行：总预算与分类预算
    budget_items = []
    total_budget = 0.0
    for b in budgets:
        amt = float(b["amount"] or 0)
        total_budget += amt
        spent = 0.0
        if b["category_id"] is None:
            spent = expense
        else:
            for c in cats:
                if c["id"] == b["category_id"]:
                    spent = float(c["spent"])
                    break
        budget_items.append(
            {
                "id": b["id"],
                "category_id": b["category_id"],
                "category_name": b["category_name"] or "总预算",
                "category_icon": b["category_icon"] or "🎯",
                "amount": amt,
                "spent": round(spent, 2),
                "pct": round(spent / amt * 100, 1) if amt else 0,
            }
        )
    return {
        "month": month,
        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(income - expense, 2),
        "category_spend": [
            {"id": c["id"], "name": c["name"], "icon": c["icon"], "spent": round(float(c["spent"]), 2)}
            for c in cats
        ],
        "budgets": budget_items,
    }


async def get_finance_analysis() -> dict[str, Any]:
    """本月 / 上月 / 当年 数据分析：收支、环比、当年月度分布。"""
    pool = _ensure_pool()
    if pool is None:
        return {}
    tz = timezone.utc
    now = datetime.now(tz).astimezone()
    cur_month = now.strftime("%Y-%m")
    prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    year = now.year

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT to_char(series.m, 'YYYY-MM') AS month,
                   COALESCE(SUM(CASE WHEN t.txn_type = 'income' THEN t.amount ELSE 0 END), 0) AS income,
                   COALESCE(SUM(CASE WHEN t.txn_type = 'expense' THEN t.amount ELSE 0 END), 0) AS expense
            FROM generate_series(
                make_date($1, 1, 1),
                make_date($1, 12, 1),
                interval '1 month'
            ) AS series(m)
            LEFT JOIN finance_transactions t
              ON date_trunc('month', t.txn_date AT TIME ZONE 'Asia/Shanghai') = series.m
            GROUP BY series.m
            ORDER BY series.m
            """,
            year,
        )
        cats_cur = await conn.fetch(
            """
            SELECT c.id, c.name, c.icon, COALESCE(SUM(t.amount), 0) AS spent
            FROM finance_categories c
            JOIN finance_transactions t ON t.category_id = c.id AND t.txn_type = 'expense'
            WHERE to_char(t.txn_date AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM') = $1
            GROUP BY c.id ORDER BY spent DESC
            """,
            cur_month,
        )
        cats_prev = await conn.fetch(
            """
            SELECT c.id, c.name, c.icon, COALESCE(SUM(t.amount), 0) AS spent
            FROM finance_categories c
            JOIN finance_transactions t ON t.category_id = c.id AND t.txn_type = 'expense'
            WHERE to_char(t.txn_date AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM') = $1
            GROUP BY c.id ORDER BY spent DESC
            """,
            prev_month,
        )

    def _agg(month: str):
        for r in rows:
            if r["month"] == month:
                return {"month": month,
                        "income": round(float(r["income"] or 0), 2),
                        "expense": round(float(r["expense"] or 0), 2)}
        return {"month": month, "income": 0.0, "expense": 0.0}

    cur = _agg(cur_month)
    prev = _agg(prev_month)
    cur["balance"] = round(cur["income"] - cur["expense"], 2)
    prev["balance"] = round(prev["income"] - prev["expense"], 2)
    cur["category_spend"] = [
        {"id": c["id"], "name": c["name"], "icon": c["icon"], "spent": round(float(c["spent"]), 2)}
        for c in cats_cur
    ]
    prev["category_spend"] = [
        {"id": c["id"], "name": c["name"], "icon": c["icon"], "spent": round(float(c["spent"]), 2)}
        for c in cats_prev
    ]

    year_income = sum(float(r["income"] or 0) for r in rows)
    year_expense = sum(float(r["expense"] or 0) for r in rows)

    def _delta(cur_v: float, prev_v: float) -> float | None:
        if prev_v == 0:
            return None if cur_v == 0 else 100.0
        return round((cur_v - prev_v) / prev_v * 100, 1)

    return {
        "current_month": cur,
        "prev_month": prev,
        "year": {
            "year": year,
            "income": round(year_income, 2),
            "expense": round(year_expense, 2),
            "balance": round(year_income - year_expense, 2),
        },
        "year_monthly": [
            {"month": r["month"], "income": round(float(r["income"] or 0), 2),
             "expense": round(float(r["expense"] or 0), 2)}
            for r in rows
        ],
        "mom": {
            "expense_delta_pct": _delta(cur["expense"], prev["expense"]),
            "income_delta_pct": _delta(cur["income"], prev["income"]),
        },
    }


async def get_finance_trend(months: int = 6) -> list[dict[str, Any]]:
    """Income/expense per month for the last N months (transfers excluded)."""
    pool = _ensure_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT to_char(series.m, 'YYYY-MM') AS month,
                   COALESCE(SUM(CASE WHEN t.txn_type = 'income' THEN t.amount ELSE 0 END), 0) AS income,
                   COALESCE(SUM(CASE WHEN t.txn_type = 'expense' THEN t.amount ELSE 0 END), 0) AS expense
            FROM generate_series(
                date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai') - make_interval(months => $1 - 1),
                date_trunc('month', now() AT TIME ZONE 'Asia/Shanghai'),
                interval '1 month'
            ) AS series(m)
            LEFT JOIN finance_transactions t
              ON date_trunc('month', t.txn_date AT TIME ZONE 'Asia/Shanghai') = series.m
            GROUP BY series.m
            ORDER BY series.m
            """,
            months,
        )
    result = []
    for row in rows:
        result.append(
            {
                "month": row["month"],
                "income": round(float(row["income"] or 0), 2),
                "expense": round(float(row["expense"] or 0), 2),
            }
        )
    return result


async def upsert_finance_budget(month: str, category_id: int | None, amount: float) -> int | None:
    pool = _ensure_pool()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO finance_budgets (month, category_id, amount)
            VALUES ($1, $2, $3)
            ON CONFLICT (month, category_id) DO UPDATE SET amount = EXCLUDED.amount
            RETURNING id
            """,
            month,
            category_id,
            round(float(amount), 2),
        )
    return row["id"] if row else None


async def list_finance_budgets(month: str | None = None) -> list[dict[str, Any]]:
    pool = _ensure_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if month:
            rows = await conn.fetch(
                """
                SELECT b.id, b.month, b.category_id, b.amount, c.name AS category_name, c.icon AS category_icon
                FROM finance_budgets b LEFT JOIN finance_categories c ON c.id = b.category_id
                WHERE b.month = $1 ORDER BY b.category_id NULLS FIRST
                """,
                month,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT b.id, b.month, b.category_id, b.amount, c.name AS category_name, c.icon AS category_icon
                FROM finance_budgets b LEFT JOIN finance_categories c ON c.id = b.category_id
                ORDER BY b.month DESC, b.category_id NULLS FIRST
                """
            )
    result = []
    for row in rows:
        item = dict(row)
        item["amount"] = float(row["amount"])
        result.append(item)
    return result


async def delete_finance_budget(budget_id: int) -> bool:
    pool = _ensure_pool()
    if pool is None:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow("DELETE FROM finance_budgets WHERE id = $1 RETURNING id", budget_id)
    return row is not None
