#!/usr/bin/env bash
# One-command dev bring-up (Linux/macOS): DB → schema → API (+frontend at /)
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d db
until docker compose exec -T db pg_isready -U eros -d eros >/dev/null 2>&1; do sleep 1; done
python3 scripts/apply_schema.py
cd backend && exec python3 -m uvicorn eros.lil.app:app --host 127.0.0.1 --port 8000
