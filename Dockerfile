FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app \
    PYTHONPATH=/app/backend

WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY backend ./backend
COPY prompts ./prompts
COPY config ./config

RUN pip install --upgrade pip && pip install -e .

RUN mkdir -p /app/data/reports /app/data/jobs /app/data/saved /app/data/runs

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -f http://127.0.0.1:8080/health || exit 1

CMD ["uvicorn", "factor_backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
