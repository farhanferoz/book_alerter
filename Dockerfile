# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1: build the Vite/React frontend
# ---------------------------------------------------------------------------
FROM node:20-slim AS web-builder

WORKDIR /web

# Install JS deps first so subsequent source-only changes hit the cache.
COPY web/package.json web/package-lock.json ./
RUN npm ci --legacy-peer-deps

# Copy the rest of the FE sources and build → /web/dist
COPY web/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2: Python runtime
#
# The Playwright "noble" image ships:
#   - Ubuntu 24.04
#   - Python 3.12
#   - Pre-installed browser binaries under /ms-playwright (chromium, firefox,
#     webkit) including the OS dependencies they need to run headless.
#
# We pin to v1.59.0 to match the `playwright` Python package version in
# pyproject.toml — the browser binaries are versioned alongside the package
# and mixing versions breaks at launch time.
# ---------------------------------------------------------------------------
FROM mcr.microsoft.com/playwright/python:v1.59.0-noble AS runtime

# `uv` from the upstream image — fast, deterministic Python dep install.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

# tesseract for the Keepa-PNG numeric extractor (src/book_alerter/keepa_chart.py).
# `tesseract-ocr-eng` ships the English language data; the rest of the data
# packages are not needed. ~30 MB total — small compared to the playwright base.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_SYSTEM_PYTHON=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    BOOK_ALERTER_WEB_DIST=/app/web/dist \
    BOOK_ALERTER_DATABASE_URL=sqlite:////app/data/book_alerter.db \
    BOOK_ALERTER_CONFIG_PATH=/app/data/config.yaml

WORKDIR /app

# Install third-party deps first (no project) so this layer caches across
# source-only edits. The set of runtime deps is parsed straight out of
# pyproject.toml — keep this list in sync with `[project.dependencies]`.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache-dir \
      "fastapi>=0.115" \
      "uvicorn[standard]>=0.32" \
      "sqlmodel>=0.0.22" \
      "alembic>=1.14" \
      "pydantic>=2.10" \
      "pydantic-settings>=2.6" \
      "structlog>=24.4" \
      "httpx>=0.28" \
      "selectolax>=0.3.27" \
      "isbnlib>=3.10" \
      "apscheduler>=3.11" \
      "watchfiles>=0.24" \
      "python-multipart>=0.0.20" \
      "pyyaml>=6.0" \
      "playwright>=1.59.0,<1.60" \
      "pillow>=11.0" \
      "pytesseract>=0.3.13" \
      "numpy>=2.0"

# Copy the application source and install the project itself (no deps —
# they're already pinned above).
COPY src/ ./src/
COPY alembic.ini ./
COPY docker-entrypoint.sh ./
RUN uv pip install --system --no-cache-dir --no-deps .

# Frontend build from stage 1.
COPY --from=web-builder /web/dist ./web/dist

# Data directory: SQLite DB + config + logs land here. The compose file mounts
# a host volume over this path so state survives container recreation.
RUN mkdir -p /app/data /app/data/logs /app/data/backups \
 && chown -R pwuser:pwuser /app
VOLUME ["/app/data"]

# Drop root.
USER pwuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
