# chip-jar

ChipJar: family tracking app for chips (points), allowance, medications and other activities using FastAPI + HTMX + Azure Database for PostgreSQL.

## Features

- Household members, incentives and medications stored in Postgres
- Log chip, allowance, medication, or other activities
- View chips/allowance history and running total
- View medication counts weekly or monthly

## Tech Stack

- Python 3.12
- FastAPI
- Azure Database for PostgreSQL Flexible Server (`jsl6906`, database `personal_storage`, schema `personal_tracking`) via psycopg 3 + connection pool
- uv for dependency management

## Setup (Local)

Install uv if not already installed: <https://github.com/astral-sh/uv>

1. Copy `.env.example` to `.env` and set `DATABASE_URL` (the `personal_tracking_app` role's password is set by the server admin, see [sql/schema.sql](sql/schema.sql)).
2. Run the app:

```bash
uv sync
uv run main.py
```

### Database schema

DDL lives in [sql/schema.sql](sql/schema.sql) (idempotent). The `personal_tracking` schema and its objects are owned by the least-privilege `personal_tracking_app` login role; the app never connects as an admin.

### One-time migration from the legacy DuckDB file

```bash
uv sync --group migrate
uv run scripts/migrate_duckdb_to_postgres.py --dry-run   # verify counts/sums, then roll back
uv run scripts/migrate_duckdb_to_postgres.py             # commit
```

Pass `--source` with the path to the legacy `.duckdb` file; add `--truncate` to replace existing target rows.


### Development with Auto Reload

You can enable code auto-reload (watch mode) during development:

```bash
uv run main.py --reload
```

Or using an environment variable (equivalent):

```bash
set RELOAD=1 & uv run main.py
```

Custom host/port:

```bash
uv run main.py --host 127.0.0.1 --port 9000 --reload
```

Visit: <http://localhost:8000>

## Docker (local)

```bash
docker compose up -d --build
```

The container reads `DATABASE_URL` from `.env` via `env_file` and runs read-only as a non-root user on port `${CHIP_JAR_PORT:-8000}`.

## Deploying to MediaServer

The app runs as the `chip-jar` container in the main compose file on MediaServer, at <http://mediaserver:8000>.
The share `\\MediaServer\Docker` is `/mnt/samsung_ssd/dockerconfig` on the server.

1. Build the image and copy it to the share. This writes `\\MediaServer\Docker\chip-jar\container_files\chip-jar-<timestamp>.tar` and refreshes `update_chip_jar.sh`:

   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\publish.ps1
   ```

2. On the server, load the image and recreate the container:

   ```bash
   ssh josh@mediaserver
   bash /mnt/samsung_ssd/dockerconfig/chip-jar/update_chip_jar.sh
   ```

   The script loads the newest tar, tags it `chip-jar:latest`, deletes older tars, runs `docker compose up -d --force-recreate chip-jar`, and waits for the container to report healthy. Once it is healthy, the script removes older `chip-jar:<timestamp>` images. If the health check fails, it keeps them so you can roll back.

Check with `docker logs -f chip-jar` or <http://mediaserver:8000/health>.

Files on the server (`/mnt/samsung_ssd/dockerconfig/chip-jar/`):

| Path | Purpose |
|------|---------|
| `.env` | `DATABASE_URL` for the `personal_tracking_app` role. Edited by hand, `chmod 600`. |
| `container_files/` | Image tars from `publish.ps1`. |
| `update_chip_jar.sh` | The update script (source: [deploy/update_chip_jar.sh](deploy/update_chip_jar.sh)). |

The `chip-jar` service is defined in `/mnt/samsung_ssd/dockerconfig/docker-compose.yaml`. It is also listed in `RESTART_CONTAINERS` in `scripts/azure-pg-firewall.env` and has an entry in `dashboard/services.yaml`.

Notes:

- `.env` changes take effect only after the container is recreated; `docker restart` does not reload the env file.
- `publish.ps1` saves the image locally and then copies it, because `docker save` written straight to the SMB share can produce an empty file.

## Environment Variables

| Name | Description | Default |
|------|-------------|---------|
| DATABASE_URL | psycopg/libpq connection string for `personal_storage` (use `sslmode=require`) | required |
| DB_SCHEMA | Postgres schema holding the app tables | personal_tracking |
| DB_POOL_MIN / DB_POOL_MAX | Connection pool bounds | 1 / 4 |
| APP_TZ | Display / input timezone | America/New_York |
| LOG_FILE | Optional file to append logs to (stdout always on) | unset |
| RELOAD | Enable auto-reload in development | 0 (disabled) |
| MANAGE_PASSWORD | HTTP Basic password (any username) for the `/manage` admin page: family members (add, colour, order, hide from tracking), home-page defaults, chip presets, medications. `/manage` is not linked anywhere and returns 404 while unset. | unset |

## Project Structure

```text
app/
  settings.py    # env/.env configuration
  db.py          # psycopg pool and query helpers
  web.py         # FastAPI app and routes
  templates/     # Jinja2 templates (+ partials/ for HTMX swaps)
  static/        # CSS, app.js, vendored htmx
deploy/          # publish.ps1 (PC) + update_chip_jar.sh (server)
sql/schema.sql   # Postgres DDL (idempotent)
scripts/         # one-time DB bootstrap/migration tooling
```

## Notes

- The Postgres schema `personal_tracking` and role `personal_tracking_app` keep their original names.

- Add tests and CI workflow (expand test coverage)
- Support exporting reports (CSV/PDF)
