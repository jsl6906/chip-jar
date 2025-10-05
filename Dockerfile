FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY app ./app
COPY data ./data

RUN uv sync --frozen

EXPOSE 8000

CMD ["uv", "run", "-m", "app.web"]
