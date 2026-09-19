"""PostgreSQL data access for the personal-tracking app (psycopg 3 + connection pool)."""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterator, Optional

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from . import settings

logger = logging.getLogger(__name__)

CATEGORIES = ("incentive", "allowance", "medication", "other")

_pool: Optional[ConnectionPool] = None


def _configure(conn: psycopg.Connection) -> None:
    # Explicit search_path + UTC session so timestamptz values come back as aware UTC.
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(settings.DB_SCHEMA)))
    conn.execute("SET timezone TO 'UTC'")
    conn.commit()


def open_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not settings.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set (see .env.example)")
        _pool = ConnectionPool(
            conninfo=settings.DATABASE_URL,
            min_size=settings.DB_POOL_MIN,
            max_size=settings.DB_POOL_MAX,
            configure=_configure,
            kwargs={"application_name": "personal-tracking"},
            open=True,
        )
        _pool.wait(timeout=30)
        logger.info("Database pool opened (schema=%s)", settings.DB_SCHEMA)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("Database pool closed")


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection; commits on success, rolls back on error."""
    with open_pool().connection() as conn:
        yield conn


def ping() -> int:
    """Return household member count; raises if the database is unreachable."""
    with connection() as conn:
        row = conn.execute("SELECT COUNT(*) FROM household_member").fetchone()
    return int(row[0]) if row else 0


# --- lookups -----------------------------------------------------------------

def list_household_members() -> list[tuple[int, str]]:
    with connection() as conn:
        return conn.execute("SELECT id, name FROM household_member ORDER BY name").fetchall()


def list_incentives() -> list[tuple[str, Optional[str], int]]:
    with connection() as conn:
        return conn.execute(
            """--sql
            SELECT name, description, points
            FROM incentives
            ORDER BY ABS(points) ASC, points DESC, name
            """
        ).fetchall()


def list_medications() -> list[tuple[str, Optional[str], int]]:
    with connection() as conn:
        return conn.execute(
            """--sql
            SELECT name, description, dose
            FROM medications
            ORDER BY ABS(dose) ASC, dose DESC, name
            """
        ).fetchall()


# --- writes ------------------------------------------------------------------

def add_activity(
    household_member_id: int,
    category: str,
    activity_type: str,
    amount: Optional[Decimal],
    notes: Optional[str] = None,
    ts: Optional[datetime] = None,
) -> int:
    """Insert an activity row and return its id.

    When amount is None for incentive/medication, the default is derived from the lookup table.
    """
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}")
    if ts is None:
        ts = datetime.now(timezone.utc)
    with connection() as conn:
        if amount is None and category in ("incentive", "medication"):
            table, col = ("incentives", "points") if category == "incentive" else ("medications", "dose")
            row = conn.execute(
                sql.SQL("SELECT {} FROM {} WHERE name = %s").format(sql.Identifier(col), sql.Identifier(table)),
                [activity_type],
            ).fetchone()
            if row:
                amount = Decimal(row[0])
        row = conn.execute(
            """--sql
            INSERT INTO activity (ts, household_member_id, category, activity_type, amount, notes)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            [ts, household_member_id, category, activity_type, amount, notes],
        ).fetchone()
    return int(row[0])


def delete_activity(activity_id: int) -> int:
    """Delete an activity row by id. Returns number of rows deleted."""
    with connection() as conn:
        cur = conn.execute("DELETE FROM activity WHERE id = %s", [activity_id])
        return cur.rowcount


# --- reports -----------------------------------------------------------------

def _category_report(household_member_id: int, category: str, limit: int = 50):
    """Return (rows, total_all, prior_balance, prior_count) for a running-balance category.

    rows: (id, ts, local_ts, activity_type, amount, notes, running_total), newest first.
    running_total for each row is the balance *after* that row was applied.
    """
    with connection() as conn:
        total_row = conn.execute(
            """--sql
            SELECT COALESCE(SUM(amount), 0) AS total_all, COUNT(*) AS cnt
            FROM activity
            WHERE household_member_id = %s AND category = %s
            """,
            [household_member_id, category],
        ).fetchone()
        total_all: Decimal = total_row[0]
        total_count: int = total_row[1]

        limited_rows = conn.execute(
            """--sql
            WITH limited AS (
                SELECT id, ts, activity_type, amount, notes
                FROM activity
                WHERE household_member_id = %s AND category = %s
                ORDER BY ts DESC, id DESC
                LIMIT %s
            )
            SELECT id, ts, activity_type, amount, notes,
                   SUM(COALESCE(amount, 0)) OVER (
                       ORDER BY ts DESC, id DESC
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cum_from_newest
            FROM limited
            ORDER BY ts DESC, id DESC
            """,
            [household_member_id, category, limit],
        ).fetchall()

    if not limited_rows:
        return [], total_all, Decimal(0), 0

    slice_sum = Decimal(0)
    processed = []
    for _id, ts, activity_type, amount, notes, cum_from_newest in limited_rows:
        amt_val = amount if amount is not None else Decimal(0)
        slice_sum += amt_val
        running_total = total_all - (cum_from_newest - amt_val)
        local_ts = ts.astimezone(settings.LOCAL_TZ)
        processed.append((_id, ts, local_ts, activity_type, amount, notes, running_total))

    prior_balance = total_all - slice_sum
    prior_count = max(total_count - len(limited_rows), 0)
    return processed, total_all, prior_balance, prior_count


def incentive_report(household_member_id: int, limit: int = 50):
    return _category_report(household_member_id, "incentive", limit=limit)


def allowance_report(household_member_id: int, limit: int = 50):
    return _category_report(household_member_id, "allowance", limit=limit)


def medication_summary(household_member_id: int, period: str = "week"):
    """Return (period, medication_name, count, latest_notes) grouped by ISO week or month in local time."""
    if period not in {"week", "month"}:
        period = "week"
    fmt = "IYYY-IW" if period == "week" else "YYYY-MM"
    with connection() as conn:
        return conn.execute(
            """--sql
            WITH base AS (
                SELECT to_char(ts AT TIME ZONE %s, %s) AS period, activity_type, notes, ts
                FROM activity
                WHERE household_member_id = %s AND category = 'medication'
            )
            SELECT period,
                   activity_type,
                   COUNT(*) AS count,
                   (ARRAY_AGG(notes ORDER BY ts DESC))[1] AS latest_notes
            FROM base
            GROUP BY period, activity_type
            ORDER BY period DESC, activity_type
            LIMIT 100
            """,
            [settings.LOCAL_TZ.key, fmt, household_member_id],
        ).fetchall()


def other_activity_report(household_member_id: int, limit: int = 100):
    """Return (rows, count, total_amount) for 'other' activities, newest first."""
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT ts, activity_type, amount, notes
            FROM activity
            WHERE household_member_id = %s AND category = 'other'
            ORDER BY ts DESC, id DESC
            LIMIT %s
            """,
            [household_member_id, limit],
        ).fetchall()
        agg = conn.execute(
            """--sql
            SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total_amount
            FROM activity
            WHERE household_member_id = %s AND category = 'other'
            """,
            [household_member_id],
        ).fetchone()
    return rows, agg[0], agg[1]


def category_totals_for_members(category: str) -> dict[int, Decimal]:
    """Return {member_id: total_amount} for every member (zero where none)."""
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT hm.id, COALESCE(SUM(a.amount), 0) AS total
            FROM household_member hm
            LEFT JOIN activity a
                   ON a.household_member_id = hm.id AND a.category = %s
            GROUP BY hm.id
            """,
            [category],
        ).fetchall()
    return {mid: total for mid, total in rows}
