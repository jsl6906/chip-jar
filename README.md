# Personal Tracking Application

Basic family tracking app for incentives (points) and medications using DuckDB + FastAPI.

## Features

- Manage household members (seeded via CSV)
- Log incentive, medication, or other activities
- View incentive history and running total
- View medication counts weekly or monthly

## Tech Stack

- Python 3.12
- FastAPI
- DuckDB (local file, path configurable via `DATA_DIR` env var)
- uv for dependency management

## Setup (Local)

Install uv if not already installed: <https://github.com/astral-sh/uv>

Run the app:

```bash
uv sync
uv run main.py
```

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

Persistent DuckDB storage is mounted to the named volume `personal_tracking_data`.

## Environment Variables

| Name | Description | Default |
|------|-------------|---------|
| DATA_DIR | Directory to store DuckDB database | ./data (host) / /data (container) |
| RELOAD | Enable auto-reload in development | 0 (disabled) |

## Project Structure

```text
app/
  db.py          # DB setup and query helpers
  web.py         # FastAPI app and routes
  templates/     # Jinja2 templates
  static/        # CSS/static assets
data/            # Seed CSV files
```

## Notes / Next Steps

- Add authentication/authorization for multi-user deployments
- Add edit/delete activity capability
- Add API endpoints (JSON) for potential frontend expansion
- Add tests and CI workflow
- Support exporting reports (CSV/PDF)
