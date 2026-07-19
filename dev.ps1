# One-command dev bring-up (Windows PowerShell): DB → schema → API
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
docker compose up -d db
do { Start-Sleep 1 } until ((docker compose exec -T db pg_isready -U eros -d eros) -match "accepting")
python scripts/apply_schema.py
Set-Location backend
python -m uvicorn eros.lil.app:app --host 127.0.0.1 --port 8000
