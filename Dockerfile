FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./

# Pre-generate lock (layer cache) then install deps
RUN uv lock || true
RUN uv sync

# Install curl for health check
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY data ./data

EXPOSE 8000

# Add health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info", "--access-log"]
