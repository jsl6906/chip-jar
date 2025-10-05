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

COPY app ./app
COPY data ./data

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
