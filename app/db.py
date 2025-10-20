import os
import duckdb
from pathlib import Path
from datetime import datetime
from typing import Optional
import logging

logger = logging.getLogger(__name__)

DATA_DIR_ENV = "DATA_DIR"
DEFAULT_DATA_DIR = Path("data")
DB_FILENAME = "personal_tracking.duckdb"

SCHEMA_STATEMENTS = [
    """--sql
    CREATE TABLE IF NOT EXISTS household_member (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    );
    """,
    """--sql
    CREATE TABLE IF NOT EXISTS incentives (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        points INTEGER NOT NULL
    );
    """,
    """--sql
    CREATE TABLE IF NOT EXISTS medications (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        dose INTEGER NOT NULL
    );
    """,
    """--sql
    CREATE TABLE IF NOT EXISTS activity (
        id BIGINT PRIMARY KEY,
        ts TIMESTAMP NOT NULL,
        household_member_id INTEGER NOT NULL REFERENCES household_member(id),
        category TEXT NOT NULL,
        activity_type TEXT NOT NULL,
        amount INTEGER,
        notes TEXT
    );
    """,
    """--sql
    CREATE INDEX IF NOT EXISTS idx_activity_member_ts ON activity(household_member_id, ts);
    """,
]


def get_data_dir() -> Path:
    p = Path(os.environ.get(DATA_DIR_ENV, DEFAULT_DATA_DIR)).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_db_path() -> Path:
    return get_data_dir() / DB_FILENAME


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(get_db_path()))


def init_db() -> None:
    logger.info(f"Initializing database at {get_db_path()}")
    con = connect()
    logger.info("Connected to database successfully")
    
    for i, stmt in enumerate(SCHEMA_STATEMENTS):
        logger.debug(f"Executing schema statement {i+1}/{len(SCHEMA_STATEMENTS)}")
        con.execute(stmt)
    # Migration: drop obsolete dob column if present
    try:
        cols = con.execute("PRAGMA table_info('household_member')").fetchall()
        if any(c[1] == 'dob' for c in cols):
            con.execute("ALTER TABLE household_member DROP COLUMN dob")
    except Exception:
        pass
    # seed tables from CSV if empty
    logger.info("Starting CSV data sync...")
    seed_from_csv(con)
    logger.info("Database initialization complete")


def table_empty(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    if not row:
        return True
    return (row[0] or 0) == 0


def seed_from_csv(con: duckdb.DuckDBPyConnection):
    data_dir = get_data_dir()
    pkg_data_dir = Path(__file__).resolve().parent.parent / "data"

    def csv_path(name: str) -> Path:
        # Prefer external / mounted data dir; fallback to packaged copy if missing
        primary = data_dir / name
        if primary.exists():
            return primary
        fallback = pkg_data_dir / name
        return fallback
    # Helper functions to sync CSV content (add new + update changed attributes) without deleting existing rows.
    def sync_household():
        # If empty, fast path insert to assign contiguous ids.
        if table_empty(con, "household_member"):
            con.execute(f"""--sql
                INSERT INTO household_member(id, name)
                SELECT row_number() OVER () as id, name
                FROM read_csv_auto('{csv_path('household_member.csv')}', HEADER=TRUE)
            """)
            return
        # Otherwise only insert new names (no attributes beyond name to update).
        con.execute(f"""--sql
            CREATE TEMPORARY TABLE tmp_hh AS
            SELECT DISTINCT src.name AS name
            FROM read_csv_auto('{csv_path('household_member.csv')}', HEADER=TRUE) AS src;
        """)
        # Insert new names assigning new ids after current max.
        con.execute("""--sql
            WITH max_id AS (SELECT COALESCE(MAX(id),0) AS m FROM household_member),
                 new_rows AS (
                     SELECT t.name AS name, ROW_NUMBER() OVER (ORDER BY t.name) AS rn
                     FROM tmp_hh t
                     LEFT JOIN household_member h ON h.name = t.name
                     WHERE h.name IS NULL
                 )
            INSERT INTO household_member(id, name)
            SELECT max_id.m + rn, name FROM new_rows CROSS JOIN max_id;
        """)
        con.execute("DROP TABLE IF EXISTS tmp_hh")

    def sync_incentives():
        if table_empty(con, "incentives"):
            con.execute(f"""--sql
                INSERT INTO incentives(id, name, description, points)
                SELECT row_number() OVER () as id, name, description, TRY_CAST(points AS INTEGER)
                FROM read_csv_auto('{csv_path('incentives.csv')}', HEADER=TRUE)
            """)
            return
        con.execute(f"""--sql
            CREATE TEMPORARY TABLE tmp_incentive AS
            SELECT name, description, TRY_CAST(points AS INTEGER) AS points
            FROM read_csv_auto('{csv_path('incentives.csv')}', HEADER=TRUE);
        """)
        # Update changed rows
        con.execute("""--sql
            UPDATE incentives AS i
            SET description = t.description,
                points = t.points
            FROM tmp_incentive t
            WHERE i.name = t.name
              AND (coalesce(i.description,'') <> coalesce(t.description,'') OR i.points <> t.points);
        """)
        # Insert new rows
        con.execute("""--sql
            WITH max_id AS (SELECT COALESCE(MAX(id),0) AS m FROM incentives),
                 to_insert AS (
                   SELECT t.name, t.description, t.points,
                          ROW_NUMBER() OVER (ORDER BY t.name) AS rn
                   FROM tmp_incentive t
                   LEFT JOIN incentives i ON i.name = t.name
                   WHERE i.name IS NULL
                 )
            INSERT INTO incentives(id, name, description, points)
            SELECT max_id.m + rn, name, description, points
            FROM to_insert CROSS JOIN max_id;
        """)
        con.execute("DROP TABLE IF EXISTS tmp_incentive")

    def sync_medications():
        if table_empty(con, "medications"):
            con.execute(f"""--sql
                INSERT INTO medications(id, name, description, dose)
                SELECT row_number() OVER () as id, name, description, TRY_CAST(dose AS INTEGER)
                FROM read_csv_auto('{csv_path('medications.csv')}', HEADER=TRUE)
            """)
            return
        con.execute(f"""--sql
            CREATE TEMPORARY TABLE tmp_med AS
            SELECT name, description, TRY_CAST(dose AS INTEGER) AS dose
            FROM read_csv_auto('{csv_path('medications.csv')}', HEADER=TRUE);
        """)
        # Update changed rows
        con.execute("""--sql
            UPDATE medications AS m
            SET description = t.description,
                dose = t.dose
            FROM tmp_med t
            WHERE m.name = t.name
              AND (coalesce(m.description,'') <> coalesce(t.description,'') OR m.dose <> t.dose);
        """)
        # Insert new rows
        con.execute("""--sql
            WITH max_id AS (SELECT COALESCE(MAX(id),0) AS m FROM medications),
                 to_insert AS (
                   SELECT t.name, t.description, t.dose,
                          ROW_NUMBER() OVER (ORDER BY t.name) AS rn
                   FROM tmp_med t
                   LEFT JOIN medications m ON m.name = t.name
                   WHERE m.name IS NULL
                 )
            INSERT INTO medications(id, name, description, dose)
            SELECT max_id.m + rn, name, description, dose
            FROM to_insert CROSS JOIN max_id;
        """)
        con.execute("DROP TABLE IF EXISTS tmp_med")

    # Execute syncs
    sync_household()
    sync_incentives()
    sync_medications()


def add_activity(household_member_id: int, category: str, activity_type: str, amount: Optional[int], notes: Optional[str] = None, ts: Optional[datetime] = None):
    con = connect()
    if amount is None:
        # derive default amount from lookup tables for incentives/medications
        if category == "incentive":
            row = con.execute("SELECT points FROM incentives WHERE name = ?", [activity_type]).fetchone()
            if row:
                amount = row[0]
        elif category == "medication":
            row = con.execute("SELECT dose FROM medications WHERE name = ?", [activity_type]).fetchone()
            if row:
                amount = row[0]
    if ts is None:
        ts = datetime.utcnow()
    con.execute(
        """--sql
        INSERT INTO activity(id, ts, household_member_id, category, activity_type, amount, notes)
        VALUES ((SELECT COALESCE(MAX(id)+1,1) FROM activity), ?, ?, ?, ?, ?, ?)
        """,
        [ts, household_member_id, category, activity_type, amount, notes],
    )


def list_household_members():
    con = connect()
    return con.execute("SELECT id, name FROM household_member ORDER BY name").fetchall()


def list_incentives():
    con = connect()
    return con.execute(
        """--sql
        SELECT name, description, points
        FROM incentives
        ORDER BY ABS(points) ASC, points DESC, name
        """
    ).fetchall()


def list_medications():
    con = connect()
    return con.execute(
        """--sql
        SELECT name, description, dose
        FROM medications
        ORDER BY ABS(dose) ASC, dose DESC, name
        """
    ).fetchall()


def incentive_report(household_member_id: int, limit: int = 50):
    """Return recent incentive history plus overall total and running totals.

    Running total logic (Option B):
      - Compute overall total once (total_all).
      - Pull limited most recent rows (DESC) with window cumulative sum from newest downward.
      - Derive running_total for each row as: total_all - (cum_from_newest - amount_for_row)
        ensuring the newest row's running_total == overall total.
    Also returns prior_balance (total_all - sum(displayed)) and prior_count (earlier row count).
    """
    con = connect()
    total_row = con.execute(
        """--sql
        SELECT COALESCE(SUM(amount),0) AS total_all, COUNT(*) AS cnt
        FROM activity
        WHERE household_member_id = ? AND category = 'incentive'
        """,
        [household_member_id],
    ).fetchone()
    total_all = total_row[0] if total_row else 0
    total_count = total_row[1] if total_row else 0

    # Fetch limited set with cumulative sum from newest downward
    limited_rows = con.execute(
        """--sql
        WITH limited AS (
            SELECT id, ts, activity_type, amount, notes
            FROM activity
            WHERE household_member_id = ? AND category = 'incentive'
            ORDER BY ts DESC, id DESC
            LIMIT ?
        )
        SELECT id, ts, activity_type, amount, notes,
               SUM(COALESCE(amount,0)) OVER (ORDER BY ts DESC, id DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_from_newest
        FROM limited
        ORDER BY ts DESC, id DESC;
        """,
        [household_member_id, limit],
    ).fetchall()

    if not limited_rows:
        return [], total_all, 0, 0

    # Compute slice sum & running totals
    slice_sum = 0
    processed = []  # (id, ts, activity_type, amount, notes, running_total)
    for row in limited_rows:
        _id, ts, activity_type, amount, notes, cum_from_newest = row
        amt_val = amount if amount is not None else 0
        slice_sum += amt_val
        running_total = total_all - (cum_from_newest - amt_val)
        processed.append((_id, ts, activity_type, amount, notes, running_total))

    prior_balance = total_all - slice_sum
    prior_count = max(total_count - len(limited_rows), 0)
    return processed, total_all, prior_balance, prior_count


def delete_activity(activity_id: int) -> int:
    """Delete an activity row by id. Returns number of rows deleted."""
    con = connect()
    res = con.execute("""--sql
        DELETE FROM activity WHERE id = ?
        """, [activity_id])
    # duckdb python API returns cursor; rowcount may not always be available; try to infer
    try:
        return res.rowcount if hasattr(res, 'rowcount') else 1
    except Exception:
        return 1


def medication_summary(household_member_id: int, period: str = "week"):
    """Return (period, medication_name, count, latest_notes) for the chosen period grouping."""
    con = connect()
    if period not in {"week", "month"}:
        period = "week"
    grp_expr = "strftime('%Y-%W', ts)" if period == "week" else "strftime('%Y-%m', ts)"
    rows = con.execute(
        f"""--sql
        WITH base AS (
            SELECT {grp_expr} AS period, activity_type, notes, ts
            FROM activity
            WHERE household_member_id = ? AND category = 'medication'
        ), counts AS (
            SELECT period, activity_type, COUNT(*) AS count
            FROM base
            GROUP BY 1,2
        ), latest AS (
            SELECT period, activity_type,
                   ANY_VALUE(notes) FILTER (WHERE ts = max_ts) AS latest_notes
            FROM (
                SELECT period, activity_type, notes, ts,
                       MAX(ts) OVER (PARTITION BY period, activity_type) AS max_ts
                FROM base
            ) x
            GROUP BY period, activity_type
        )
        SELECT c.period, c.activity_type, c.count, l.latest_notes
        FROM counts c
        LEFT JOIN latest l USING (period, activity_type)
        ORDER BY c.period DESC, c.activity_type
        LIMIT 100
        """,
        [household_member_id],
    ).fetchall()
    return rows


def other_activity_report(household_member_id: int, limit: int = 100):
    """Return list of 'other' activities (most recent first) plus aggregate counts and sum.

    Each row: (timestamp, activity_type, amount, notes)
    """
    con = connect()
    rows = con.execute(
        """--sql
        SELECT ts, activity_type, amount, notes
        FROM activity
        WHERE household_member_id = ? AND category = 'other'
        ORDER BY ts DESC
        LIMIT ?
        """,
        [household_member_id, limit],
    ).fetchall()
    agg = con.execute(
        """--sql
        SELECT COUNT(*) AS cnt, COALESCE(SUM(amount),0) AS total_amount
        FROM activity
        WHERE household_member_id = ? AND category = 'other'
        """,
        [household_member_id],
    ).fetchone()
    count = agg[0] if agg else 0
    total_amt = agg[1] if agg else 0
    return rows, count, total_amt


def incentive_total(household_member_id: int) -> int:
    """Return total incentive points for a single member."""
    con = connect()
    row = con.execute(
        """--sql
        SELECT COALESCE(SUM(amount),0) FROM activity
        WHERE household_member_id = ? AND category = 'incentive'
        """,
        [household_member_id],
    ).fetchone()
    return row[0] if row else 0


def incentive_totals_for_members() -> list[tuple[int, int]]:
    """Return list of (member_id, total_points) for all members (zero where none)."""
    con = connect()
    return con.execute(
        """--sql
        SELECT hm.id, COALESCE(SUM(a.amount),0) AS total
        FROM household_member hm
        LEFT JOIN activity a ON a.household_member_id = hm.id AND a.category = 'incentive'
        GROUP BY hm.id, hm.name
        ORDER BY hm.name
        """
    ).fetchall()
