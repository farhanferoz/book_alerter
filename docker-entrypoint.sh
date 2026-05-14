#!/usr/bin/env bash
# Entry point for the book_alerter container.
# Runs Alembic migrations against the data-volume DB, then launches uvicorn.
set -euo pipefail

# Ensure the data directory exists on the mounted volume.
mkdir -p /app/data

# Apply DB migrations idempotently — safe on first boot and on upgrades.
echo "[entrypoint] running alembic upgrade head"
alembic -c /app/alembic.ini upgrade head

echo "[entrypoint] starting uvicorn"
exec uvicorn book_alerter.app:app --host 0.0.0.0 --port 8000
