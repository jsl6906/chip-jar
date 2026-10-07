"""PostgreSQL data access for ChipJar (psycopg 3 + connection pool)."""

import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator, Optional

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
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
            kwargs={"application_name": "chip-jar"},
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


def delete_activity(activity_id: int) -> Optional[dict]:
    """Delete an activity row by id and return it (None if it didn't exist)."""
    with connection() as conn:
        row = conn.execute(
            """--sql
            DELETE FROM activity WHERE id = %s
            RETURNING id, ts, household_member_id, category, activity_type, amount, notes, created_at
            """,
            [activity_id],
        ).fetchone()
    if row is None:
        return None
    keys = ("id", "ts", "household_member_id", "category", "activity_type", "amount", "notes", "created_at")
    return dict(zip(keys, row))


def restore_activity(row: dict) -> bool:
    """Re-insert a previously deleted row with its original id. Returns False if the id is taken."""
    if row["category"] not in CATEGORIES:
        raise ValueError(f"Unknown category {row['category']!r}")
    with connection() as conn:
        cur = conn.execute(
            """--sql
            INSERT INTO activity (id, ts, household_member_id, category, activity_type, amount, notes, created_at)
            VALUES (%(id)s, %(ts)s, %(household_member_id)s, %(category)s, %(activity_type)s, %(amount)s, %(notes)s, %(created_at)s)
            ON CONFLICT (id) DO NOTHING
            """,
            row,
        )
        return cur.rowcount == 1


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


def activity_list(household_member_id: int, category: str, limit: int = 100):
    """Return (rows, count, total_amount) for a category, newest first.

    rows: (id, ts, activity_type, amount, notes).
    """
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT id, ts, activity_type, amount, notes
            FROM activity
            WHERE household_member_id = %s AND category = %s
            ORDER BY ts DESC, id DESC
            LIMIT %s
            """,
            [household_member_id, category, limit],
        ).fetchall()
        agg = conn.execute(
            """--sql
            SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total_amount
            FROM activity
            WHERE household_member_id = %s AND category = %s
            """,
            [household_member_id, category],
        ).fetchone()
    return rows, agg[0], agg[1]


def dashboard(days: int = 14) -> list[dict]:
    """Per-member summary for the home screen.

    Each dict: id, name, chips, allowance, last (dict or None), series (list[Decimal] of the
    daily chips balance for the trailing `days` days, oldest first).
    """
    with connection() as conn:
        members = conn.execute("SELECT id, name FROM household_member ORDER BY name").fetchall()
        totals = conn.execute(
            """--sql
            SELECT household_member_id, category, COALESCE(SUM(amount), 0)
            FROM activity
            WHERE category IN ('incentive', 'allowance')
            GROUP BY household_member_id, category
            """
        ).fetchall()
        last_rows = conn.execute(
            """--sql
            SELECT DISTINCT ON (household_member_id)
                   household_member_id, ts, category, activity_type, amount
            FROM activity
            ORDER BY household_member_id, ts DESC, id DESC
            """
        ).fetchall()
        daily = conn.execute(
            """--sql
            WITH cutoff AS (
                SELECT (date_trunc('day', now() AT TIME ZONE %(tz)s) - make_interval(days => %(days)s - 1)) AS start_local
            )
            SELECT a.household_member_id,
                   (a.ts AT TIME ZONE %(tz)s)::date AS day,
                   SUM(COALESCE(a.amount, 0)) AS net
            FROM activity a, cutoff c
            WHERE a.category = 'incentive'
              AND (a.ts AT TIME ZONE %(tz)s) >= c.start_local
            GROUP BY a.household_member_id, day
            """,
            {"tz": settings.LOCAL_TZ.key, "days": days},
        ).fetchall()

    chips = {mid: amt for mid, cat, amt in totals if cat == "incentive"}
    allowance = {mid: amt for mid, cat, amt in totals if cat == "allowance"}
    last = {row[0]: {"ts": row[1], "category": row[2], "activity_type": row[3], "amount": row[4]} for row in last_rows}

    today = datetime.now(settings.LOCAL_TZ).date()
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    net_by_member: dict[int, dict] = {}
    for mid, day, net in daily:
        net_by_member.setdefault(mid, {})[day] = net

    result = []
    for mid, name in members:
        total = chips.get(mid, Decimal(0))
        nets = net_by_member.get(mid, {})
        window_sum = sum(nets.values(), Decimal(0))
        balance = total - window_sum
        series = []
        for day in day_list:
            balance += nets.get(day, Decimal(0))
            series.append(balance)
        result.append(
            {
                "id": mid,
                "name": name,
                "chips": total,
                "allowance": allowance.get(mid, Decimal(0)),
                "last": last.get(mid),
                "series": series,
            }
        )
    return result


def frequent_items(household_member_id: int, category: str, limit: int = 8, days: int = 120):
    """Most-used (activity_type, amount) pairs for a member/category, scored by frequency and recency.

    Returns rows of (activity_type, amount, uses).
    """
    with connection() as conn:
        return conn.execute(
            """--sql
            SELECT btrim(activity_type) AS activity_type,
                   amount,
                   COUNT(*) AS uses
            FROM activity
            WHERE household_member_id = %s
              AND category = %s
              AND ts >= now() - make_interval(days => %s)
              AND btrim(activity_type) <> ''
            GROUP BY btrim(activity_type), amount
            -- each week since last use halves an item's weight
            ORDER BY COUNT(*) / (1 + EXTRACT(EPOCH FROM now() - MAX(ts)) / 604800.0) DESC,
                     COUNT(*) DESC,
                     1
            LIMIT %s
            """,
            [household_member_id, category, days, limit],
        ).fetchall()


def medication_grid(household_member_id: int, start: date, days: int = 7) -> dict[tuple[str, date], int]:
    """Return {(medication, local_date): doses} for `days` local days beginning at `start`."""
    tz = settings.LOCAL_TZ.key
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT btrim(activity_type) AS med,
                   (ts AT TIME ZONE %(tz)s)::date AS day,
                   COUNT(*) AS doses
            FROM activity
            WHERE household_member_id = %(member)s
              AND category = 'medication'
              AND ts >= (%(start)s::date)::timestamp AT TIME ZONE %(tz)s
              AND ts <  (%(start)s::date + %(days)s)::timestamp AT TIME ZONE %(tz)s
            GROUP BY 1, 2
            """,
            {"tz": tz, "member": household_member_id, "start": start, "days": days},
        ).fetchall()
    return {(med, day): doses for med, day, doses in rows}


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


# --- spinners ----------------------------------------------------------------

SPINNER_KINDS = ("generic", "movie", "restaurant")


def list_spinners() -> list[dict]:
    """All spinners with pick counts and their latest result (if any)."""
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT s.id, s.name, s.emoji, s.kind,
                   (SELECT COUNT(*) FROM spinner_pick p WHERE p.spinner_id = s.id) AS picks,
                   r.label, r.ts
            FROM spinner s
            LEFT JOIN LATERAL (
                SELECT label, ts FROM spin_result WHERE spinner_id = s.id ORDER BY ts DESC, id DESC LIMIT 1
            ) r ON TRUE
            ORDER BY s.created_at, s.id
            """
        ).fetchall()
    return [
        {"id": r[0], "name": r[1], "emoji": r[2], "kind": r[3], "picks": r[4], "last_label": r[5], "last_ts": r[6]}
        for r in rows
    ]


def get_spinner(spinner_id: int) -> Optional[dict]:
    with connection() as conn:
        row = conn.execute("SELECT id, name, emoji, kind FROM spinner WHERE id = %s", [spinner_id]).fetchone()
    return {"id": row[0], "name": row[1], "emoji": row[2], "kind": row[3]} if row else None


def create_spinner(name: str, emoji: str, kind: str) -> int:
    if kind not in SPINNER_KINDS:
        raise ValueError(f"Unknown spinner kind {kind!r}")
    with connection() as conn:
        row = conn.execute(
            """--sql
            INSERT INTO spinner (name, emoji, kind) VALUES (%s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET emoji = EXCLUDED.emoji
            RETURNING id
            """,
            [name, emoji, kind],
        ).fetchone()
    return int(row[0])


def delete_spinner(spinner_id: int) -> bool:
    with connection() as conn:
        cur = conn.execute("DELETE FROM spinner WHERE id = %s", [spinner_id])
        return cur.rowcount == 1


def spinner_picks(spinner_id: int) -> list[dict]:
    """Current round's picks, one per member, in roster order (matches list_household_members)."""
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT p.household_member_id, hm.name, p.label, p.jellyfin_item_id, p.place_id
            FROM spinner_pick p
            JOIN household_member hm ON hm.id = p.household_member_id
            WHERE p.spinner_id = %s
            ORDER BY hm.name
            """,
            [spinner_id],
        ).fetchall()
    return [{"member_id": r[0], "member": r[1], "label": r[2], "jellyfin_item_id": r[3], "place_id": r[4]} for r in rows]


def set_pick(
    spinner_id: int,
    household_member_id: int,
    label: str,
    jellyfin_item_id: Optional[str] = None,
    place_id: Optional[str] = None,
) -> None:
    with connection() as conn:
        conn.execute(
            """--sql
            INSERT INTO spinner_pick (spinner_id, household_member_id, label, jellyfin_item_id, place_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (spinner_id, household_member_id)
            DO UPDATE SET label = EXCLUDED.label, jellyfin_item_id = EXCLUDED.jellyfin_item_id,
                          place_id = EXCLUDED.place_id, updated_at = now()
            """,
            [spinner_id, household_member_id, label, jellyfin_item_id, place_id],
        )


def clear_pick(spinner_id: int, household_member_id: int) -> None:
    with connection() as conn:
        conn.execute(
            "DELETE FROM spinner_pick WHERE spinner_id = %s AND household_member_id = %s",
            [spinner_id, household_member_id],
        )


def clear_picks(spinner_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM spinner_pick WHERE spinner_id = %s", [spinner_id])


def record_spin(spinner_id: int, winner: dict, options: list[dict]) -> int:
    with connection() as conn:
        row = conn.execute(
            """--sql
            INSERT INTO spin_result (spinner_id, label, household_member_id, jellyfin_item_id, place_id, options)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            [spinner_id, winner["label"], winner["member_id"], winner.get("jellyfin_item_id"), winner.get("place_id"), Jsonb(options)],
        ).fetchone()
    return int(row[0])


def spin_history(spinner_id: int, limit: int = 12) -> list[dict]:
    """Most recent results, newest first: id, ts, label, member, member_id, jellyfin_item_id, place_id, option_count."""
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT r.id, r.ts, r.label, hm.name, r.household_member_id, r.jellyfin_item_id, r.place_id,
                   jsonb_array_length(r.options) AS option_count
            FROM spin_result r
            LEFT JOIN household_member hm ON hm.id = r.household_member_id
            WHERE r.spinner_id = %s
            ORDER BY r.ts DESC, r.id DESC
            LIMIT %s
            """,
            [spinner_id, limit],
        ).fetchall()
    return [
        {"id": r[0], "ts": r[1], "label": r[2], "member": r[3], "member_id": r[4], "jellyfin_item_id": r[5], "place_id": r[6], "option_count": r[7]}
        for r in rows
    ]


def recent_movie_ids(days: int = 90) -> set[str]:
    """Jellyfin items that recently won a spin or are currently picked anywhere (so suggestions skip them)."""
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT jellyfin_item_id FROM spin_result
            WHERE jellyfin_item_id IS NOT NULL AND ts >= now() - make_interval(days => %s)
            UNION
            SELECT jellyfin_item_id FROM spinner_pick WHERE jellyfin_item_id IS NOT NULL
            """,
            [days],
        ).fetchall()
    return {r[0] for r in rows}


def recent_place_ids(days: int = 21) -> set[str]:
    """Restaurants that won a spin within `days` (so suggestions favour somewhere new)."""
    with connection() as conn:
        rows = conn.execute(
            """--sql
            SELECT DISTINCT place_id FROM spin_result
            WHERE place_id IS NOT NULL AND ts >= now() - make_interval(days => %s)
            """,
            [days],
        ).fetchall()
    return {r[0] for r in rows}
