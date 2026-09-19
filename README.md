# personal-tracking

Basic family tracking app for chips (points), allowance, medications and other activities using FastAPI + Azure Database for PostgreSQL.

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

Defaults to the deployed file on `\\MediaServer\Docker\personal-tracking\data`; pass `--source` to override and `--truncate` to replace existing target rows.

### Git hooks (automatic docker build after commits)

This repository includes a tracked hooks directory `.githooks` which contains cross-platform post-commit hooks that will attempt to build and save a Docker image tar after each commit.

Commands:

- Enable tracked hooks for this repo (preferred, keeps hooks under source control):

```powershell
git config core.hooksPath .githooks
```

- Install a local hook into the active hooks path (will detect `core.hooksPath` and install there):

```powershell
.\scripts\install_git_hook.ps1
```

- To disable tracked hooks and use the repository `.git/hooks` instead:

```powershell
git config --unset core.hooksPath
```

Notes:

- The `.githooks` files include POSIX, CMD and PowerShell wrappers so hooks are usable across platforms.
- The hook respects `PT_SKIP_TAR=1` to opt-out of building on a given machine.
- The build logic is implemented in `scripts/build_image_tar.ps1`. The hooks now call that script with no arguments and the build script tags images by git commit SHA (or timestamp fallback).


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

## Docker

Build and run with Docker Compose:

```bash
docker compose up --build
```

The container reads `DATABASE_URL` from `.env` via `env_file`; no data volume is required anymore.

### Local Automatic Image Archive (Optional)

This repo includes an optional local **post-commit** git hook that automatically builds the Docker image and saves a versioned tarball (no registry push required).

Enable it:

```bash
git config core.hooksPath .githooks
```

After each non-doc commit you will see a `dist/personal-tracking-<tag>-<timestamp>.tar` plus `personal-tracking-latest.tar` (copy of the most recent build). The tag includes the short commit hash (e.g. `commit-a1b2c3d`). Markdown / docs-only commits are skipped to save time.

Hook environments / overrides:

| Mechanism | Effect |
|-----------|--------|
| `PT_SKIP_TAR=1` | Skip build for that commit |
| `-Keep <n>` (hook param) | Retain only last n tars (default 10) |
| `-MultiArch` | Build multi-arch image (linux/amd64,linux/arm64) |
| `-VerboseHook` | Extra verbose logging |

Old tars beyond retention are pruned (excluding the `*-latest.tar` file).

Manually trigger (without new commit):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_image_tar.ps1 -Tag manual -Keep 5
# Multi-arch example (requires buildx)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_image_tar.ps1 -Tag ma -MultiArch
# Build image only (no tar export)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_image_tar.ps1 -Tag nosave -SkipSave
```

Transfer to a server and load there:

```bash
scp dist/personal-tracking-latest.tar user@server:/tmp/
ssh user@server "docker load -i /tmp/personal-tracking-latest.tar"
```

Disable hook (restore default hooks path):

```bash
git config --unset core.hooksPath
```

## Environment Variables

| Name | Description | Default |
|------|-------------|---------|
| DATABASE_URL | psycopg/libpq connection string for `personal_storage` (use `sslmode=require`) | required |
| DB_SCHEMA | Postgres schema holding the app tables | personal_tracking |
| DB_POOL_MIN / DB_POOL_MAX | Connection pool bounds | 1 / 4 |
| APP_TZ | Display / input timezone | America/New_York |
| LOG_FILE | Optional file to append logs to (stdout always on) | unset |
| RELOAD | Enable auto-reload in development | 0 (disabled) |

## Project Structure

```text
app/
  settings.py    # env/.env configuration
  db.py          # psycopg pool and query helpers
  web.py         # FastAPI app and routes
  templates/     # Jinja2 templates
  static/        # CSS/static assets
sql/schema.sql   # Postgres DDL (idempotent)
scripts/         # build + one-time migration tooling
```

## CI Image Artifact Workflow

A GitHub Actions workflow (`.github/workflows/docker-image-tar.yml`) builds and uploads a gzipped Docker image tar on pushes to `main` / `master` and manual dispatch.

What it does:

1. Builds image (`latest` and short SHA tags). Multi-arch optional via workflow dispatch input `multi_arch=true`.
2. Exports `personal-tracking-<sha>.tar.gz` and a `.sha256` checksum file.
3. Captures `image-id.json` (Docker image ID metadata).
4. Attempts SBOM generation (SPDX JSON) via syft.
5. Uploads artifacts with 14-day retention.

Download artifacts from the workflow run page, then:

```bash
docker load -i personal-tracking-<sha>.tar.gz
```

## Notes / Next Steps

- Add authentication/authorization for multi-user deployments
- Add edit/delete activity capability
- Add API endpoints (JSON) for potential frontend expansion
- Add tests and CI workflow (expand test coverage)
- Support exporting reports (CSV/PDF)
