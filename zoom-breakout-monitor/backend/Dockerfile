# Override to build from a registry mirror, e.g.
#   docker compose build --build-arg BASE_REGISTRY=mirror.gcr.io
ARG BASE_REGISTRY=docker.io
FROM ${BASE_REGISTRY}/library/python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# The SQLite file lives here; docker-compose mounts a named volume over it.
RUN mkdir -p /app/data

# Run unprivileged. The data directory is chowned so the volume is writable
# even on a fresh mount (a common cause of "unable to open database file").
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# --proxy-headers so the app sees the real scheme/client through nginx.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
