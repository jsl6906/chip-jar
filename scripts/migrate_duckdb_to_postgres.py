"""One-time migration: DuckDB file -> Azure PostgreSQL (personal_storage.personal_tracking).

Usage:
    uv sync --group migrate
    uv run scripts/migrate_duckdb_to_postgres.py [--source PATH] [--truncate] [--dry-run]

Reads the DuckDB file read-only, applies sql/schema.sql, bulk-loads the four tables
preserving ids, resets identity sequences, and verifies row counts / amount sums.
Target connection comes from DATABASE_URL in .env (the personal_tracking_app role).
"""

import argparse
import sys
from datetime import timezone
from pathlib import Path

import duckdb
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import settings  # noqa: E402

DEFAULT_SOURCE = r"\\MediaServer\Docker\personal-tracking\data\personal_tracking.duckdb"
SCHEMA_SQL = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"

# (table, columns) in FK-safe load order
TABLES = [
    ("household_member", ["id", "name"]),
    ("incentives", ["id", "name", "description", "points"]),
    ("medications", ["id", "name", "description", "dose"]),
    ("activity", ["id", "ts", "household_member_id", "category", "activity_type", "amount", "notes"]),
]


def fetch_source(duck: duckdb.DuckDBPyConnection, table: str, cols: list[str]) -> list[tuple]:
    rows = duck.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id").fetchall()
    if table == "activity":
        # DuckDB stored naive UTC timestamps; make them explicit for TIMESTAMPTZ.
        ts_i = cols.index("ts")
        rows = [
            tuple(v.replace(tzinfo=timezone.utc) if i == ts_i and v.tzinfo is None else v for i, v in enumerate(r))
            for r in rows
        ]
    return rows


def copy_rows(conn: psycopg.Connection, table: str, cols: list[str], rows: list[tuple]) -> None:
    stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        sql.Identifier(settings.DB_SCHEMA),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, cols)),
    )
    with conn.cursor() as cur, cur.copy(stmt) as copy:
        for r in rows:
            copy.write_row(r)


def reset_identity(conn: psycopg.Connection, table: str) -> None:
    conn.execute(
        sql.SQL(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {}.{}"
        ).format(sql.Identifier(settings.DB_SCHEMA), sql.Identifier(table)),
        [f"{settings.DB_SCHEMA}.{table}"],
    )


def target_counts(conn: psycopg.Connection) -> dict[str, int]:
    out = {}
    for table, _ in TABLES:
        row = conn.execute(
            sql.SQL("SELECT COUNT(*) FROM {}.{}").format(sql.Identifier(settings.DB_SCHEMA), sql.Identifier(table))
        ).fetchone()
        out[table] = row[0]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="Path to the DuckDB file (opened read-only)")
    ap.add_argument("--truncate", action="store_true", help="Empty target tables before loading")
    ap.add_argument("--dry-run", action="store_true", help="Load inside a transaction, then roll back")
    args = ap.parse_args()

    if not settings.DATABASE_URL:
        print("DATABASE_URL is not set; copy .env.example to .env and fill it in.", file=sys.stderr)
        return 2
    if not Path(args.source).exists():
        print(f"Source not found: {args.source}", file=sys.stderr)
        return 2

    duck = duckdb.connect(args.source, read_only=True)
    source = {t: fetch_source(duck, t, cols) for t, cols in TABLES}
    src_activity_sum = duck.execute("SELECT COALESCE(SUM(amount), 0) FROM activity").fetchone()[0]
    duck.close()
    for t, rows in source.items():
        print(f"source {t:<17} {len(rows):>6} rows")

    with psycopg.connect(settings.DATABASE_URL, application_name="personal-tracking-migrate") as conn:
        conn.execute("SET timezone TO 'UTC'")
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))

        existing = target_counts(conn)
        if any(existing.values()):
            if not args.truncate:
                print(f"Target already has data {existing}; re-run with --truncate to replace.", file=sys.stderr)
                conn.rollback()
                return 1
            conn.execute(
                sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(
                        sql.SQL("{}.{}").format(sql.Identifier(settings.DB_SCHEMA), sql.Identifier(t)) for t, _ in TABLES
                    )
                )
            )

        for table, cols in TABLES:
            copy_rows(conn, table, cols, source[table])
            reset_identity(conn, table)

        loaded = target_counts(conn)
        tgt_activity_sum = conn.execute(
            sql.SQL("SELECT COALESCE(SUM(amount), 0) FROM {}.activity").format(sql.Identifier(settings.DB_SCHEMA))
        ).fetchone()[0]

        ok = all(loaded[t] == len(source[t]) for t, _ in TABLES) and tgt_activity_sum == src_activity_sum
        for t, _ in TABLES:
            print(f"target {t:<17} {loaded[t]:>6} rows")
        print(f"activity amount sum: source={src_activity_sum} target={tgt_activity_sum}")

        if not ok:
            print("Verification FAILED; rolling back.", file=sys.stderr)
            conn.rollback()
            return 1
        if args.dry_run:
            print("Dry run: verification passed; rolling back.")
            conn.rollback()
            return 0
        conn.commit()
        print("Migration committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
