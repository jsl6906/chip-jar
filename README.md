# Personal Tracking Application

Basic family tracking app for chips (points) and medications using DuckDB + FastAPI.

## Features

- Manage household members (seeded via CSV)
- Log chip, medication, or other activities
- View chips history and running total
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
