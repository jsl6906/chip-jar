# AI Coding Agent Instructions

## uv for package management, virtual environments, and running scripts

This project uses **uv** for Python dependency management and virtual environment handling. **Never use `pip` directly** - always use `uv` commands instead. When trying to test implementation use `uv run <script>` to run scripts within the virtual environment. Use `uv add <packagename>` to add new dependencies.

## Data access: Azure PostgreSQL via psycopg

The application stores its data in Azure Database for PostgreSQL (server `jsl6906`, database `personal_storage`, schema `personal_tracking`). Runtime data access goes through `app/db.py` (psycopg 3 + `psycopg_pool`); always use `with db.connection() as conn:` and parameterized queries (`%s`), never string-formatted SQL. DDL lives in `sql/schema.sql` and must stay idempotent. The app connects as the least-privilege `personal_tracking_app` role; never use an admin role in application code.

duckdb is only used for ad-hoc analysis and one-off migrations (dependency group `migrate`); it is not an application dependency.

## SQL Formatting

When writing code with multiline SQL statements, use triple quotes for the SQL string and prefix the SQL with a comment `--sql` to indicate that it is SQL code. This helps with readability and ensures proper formatting. Example:

```python
# Example of multiline SQL statement
sql_query = """--sql
SELECT * FROM my_table WHERE condition = true;
"""
```
