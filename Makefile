    # EROS v3.2 — operator entry points.
    SHELL := /bin/bash
    DSN ?= postgresql://eros:eros@127.0.0.1:5432/eros

    .PHONY: db schema verify-spec test backend frontend dev clean

    db:            ## start Postgres via docker compose
	docker compose up -d db

    schema:        ## apply db/schema.sql (interpolates EMBEDDING_DIM, ON_ERROR_STOP=1)
	python3 scripts/apply_schema.py --dsn "$(DSN)"

    verify-spec:   ## §0.1 mandate: extract fenced sql from the canonical doc, apply to a scratch DB
	python3 scripts/verify_spec.py --dsn "$(DSN)"

    test:          ## full backend suite (gates + dgk + shape + core) against live DB
	cd backend && python3 -m pytest -q

    backend:       ## run the LIL API (serves the built frontend at /)
	cd backend && python3 -m uvicorn eros.lil.app:app --host 127.0.0.1 --port 8000

    frontend:      ## rebuild the UI (requires Node 20+); output → frontend/dist
	cd frontend && npm install && npm run build

    dev: db schema backend

    clean:
	rm -rf frontend/node_modules backend/.pytest_cache
