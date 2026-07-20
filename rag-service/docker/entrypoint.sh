#!/bin/sh
set -eu

echo "=== Running database migrations ==="
alembic upgrade head

echo "=== Starting RAG service ==="
exec uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8000 --workers 1
