"""Read-only inspection of the deployed DuckDB file to find anomalous activity rows."""

import duckdb

DB = r"\\MediaServer\Docker\personal-tracking\data\personal_tracking.duckdb"

con = duckdb.connect(DB, read_only=True)

print("== activity schema ==")
print(con.execute("PRAGMA table_info('activity')").fetchall())

print("\n== rows by category ==")
print(
    con.execute(
        """--sql
        SELECT category, COUNT(*) AS cnt, MIN(amount) AS min_amt, MAX(amount) AS max_amt,
               SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS null_amts
        FROM activity GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
)

print("\n== null / odd timestamps or members ==")
print(
    con.execute(
        """--sql
        SELECT id, ts, household_member_id, category, activity_type, amount, notes
        FROM activity
        WHERE ts IS NULL
           OR household_member_id NOT IN (SELECT id FROM household_member)
           OR activity_type IS NULL
           OR category IS NULL
        ORDER BY id
        """
    ).fetchall()
)

print("\n== extreme amounts ==")
print(
    con.execute(
        """--sql
        SELECT id, ts, household_member_id, category, activity_type, amount, notes
        FROM activity
        WHERE amount IS NULL OR ABS(amount) >= 10000
        ORDER BY ABS(COALESCE(amount,0)) DESC
        LIMIT 25
        """
    ).fetchall()
)

print("\n== duplicate activity ids ==")
print(
    con.execute(
        "SELECT id, COUNT(*) FROM activity GROUP BY 1 HAVING COUNT(*) > 1"
    ).fetchall()
)

print("\n== most recent 15 rows ==")
print(
    con.execute(
        """--sql
        SELECT id, ts, household_member_id, category, activity_type, amount, notes
        FROM activity ORDER BY id DESC LIMIT 15
        """
    ).fetchall()
)

print("\n== members ==")
print(con.execute("SELECT id, name FROM household_member ORDER BY id").fetchall())
