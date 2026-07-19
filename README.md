# EROS v3.2 — Enterprise Research Operating System (Phase 1 Core Loop)

A local-first autonomous research pipeline in which the trust chain is
**welded into PostgreSQL**: a claim cannot exist without evidence
(Gate 1), a run cannot publish without verified evidence (Gate 2), every
report sentence names a claim or a disclosed template (Gate 3, citation
coverage 1.0 by construction), and a deterministic kernel — no model —
can prove a claim's numbers, dates, entities, or quotations absent from
its cited evidence (Gate 4). The gates are database triggers with the
canonical §12 error strings; the negative suite attacks all of them and
they fail closed, 15/15.

Built from `docs/EROS_v3_2_Canonical_Architecture.md` (frozen). Schema
deltas are logged in `db/AMENDMENTS.md`; implementation truth-status in
`BUILD_STATUS.md`.

## Layout
    backend/    Python 3.11+ package `eros` (LIL API, pipeline, router,
                governor, DGK, MCP server) + the four test suites
    frontend/   React/Vite UI (built output in frontend/dist — served by
                the backend; Node needed only to rebuild)
    db/         schema.sql (canonical §7.2 + trust-chain DDL) + AMENDMENTS
    config/     manifest.json (ADR-022 lineage pins) · connectors.yaml
    scripts/    apply_schema · verify_spec · build_modelfiles ·
                attest_models · dev.sh / dev.ps1
    docs/       canonical architecture · packaging notes

## Prerequisites
- **PostgreSQL 16 + pgvector** — easiest: Docker (`docker compose up -d db`).
  Native installs work; ensure the `vector` and `pgcrypto` extensions.
- **Python 3.11+**
- **Ollama** (optional but recommended) for local model mode — ROCm/CUDA/
  Metal per your hardware. Without it EROS runs in *deterministic mode*:
  extractive claims, kernel-only verification, every degradation disclosed
  in the report footer.
- **Node 20+** only if you want to rebuild the UI (`frontend/dist` ships).

## Quickstart

**Linux / macOS**
    docker compose up -d db
    cd backend && pip install -e ".[dev]"        # extras: [embeddings,ner,pdf]
    cd .. && python3 scripts/apply_schema.py
    ./scripts/dev.sh                              # or: make backend
    # open http://127.0.0.1:8000

**Windows (PowerShell)**
    docker compose up -d db
    cd backend; pip install -e ".[dev]"; cd ..
    python scripts/apply_schema.py
    .\scripts\dev.ps1
    # open http://127.0.0.1:8000

Configuration is environment-driven (`EROS_*`); see `.env.example`.
`EROS_DATA_DIR` relocates the artifact store (defaults to `~/.eros/data`).

## Enabling local models (the real thing)
    ollama pull llama3.1:8b && ollama pull qwen2.5:7b
    ollama pull granite3.1-dense:2b && ollama pull phi4-mini
    python3 scripts/build_modelfiles.py     # derived eros-* tags, num_ctx baked
    python3 scripts/attest_models.py --pin  # SHA-256 pin the GGUF digests
    python3 scripts/attest_models.py        # verify: 4× OK expected
Cross-family verification then activates (Drafter=llama3 vs
Checker=qwen2, enforced by the g05 trigger against the attested lineage
the Router records per run). Set `EROS_REQUIRE_LOCAL_GENERATION=true`
for the FR18 posture: if the GPU path is down at run start, the API
returns an explicit fast-abort choice — never a silent 3-hour fallback.

## Using it
- **UI** — Console (ask, watch the pipeline strip + Gate Ledger + event
  ledger, cancel, approve the human gate), Evidence (claims with chunk
  locators, hashes, kernel verdicts), Graph (interactive knowledge graph).
- **API** — `POST /research` (409 = FR18 choice; 202 = queued behind the
  one active run, C10), `GET /research/{id}` (+telemetry),
  `GET /research/{id}/report` (Markdown assembled from the Gate-3
  ledger), `GET /research/{id}/evidence`, `DELETE /research/{id}`
  (cooperative cancel), `POST /research/{id}/approve`, `/lil/*` contract
  endpoints, `WS /ws/events`.
- **MCP** — `python -m eros.mcp_server` (stdio): `research_start`,
  `research_status`, `evidence_query`. Sensitive initiations are audited.

## Tests (require the live DB)
    make schema && make test      # 51 tests: 15 gate attacks · 21 DGK ·
                                  # 6 graph-shape · 9 core invariants
    make verify-spec              # §0.1: fenced SQL from the canonical doc
                                  # applied to a scratch DB (fresh cluster)

## External escalation & budget
OFF by default. Enabling requires `EROS_EXTERNAL_ENABLED=true` **and** a
per-provider `*_ZDR_CONFIRMED=true` (Zero Data Retention is a hard
admission requirement) plus API keys. The Governor enforces the
$50/month ceiling with an atomic reservation ledger; idempotency keys are
written before dispatch, so a crash mid-call can never double-bill —
orphans are flagged for the operator, never retried blindly.

## Backups & durability notes
- RPO < 1h needs host jobs this repo doesn't run for you: continuous WAL
  archiving for Postgres and hourly `restic` of `$EROS_DATA_DIR/artifacts`
  to your NAS. The write path already guarantees a row never precedes its
  durable bytes.
- **Windows**: directory-metadata fsync isn't exposed by the OS; the
  store fsyncs file bytes and uses atomic `os.replace`. Slightly wider
  metadata-durability window than POSIX — disclosed, not hidden.

## Honest status
`BUILD_STATUS.md` is the ledger: what ran here (including a live
published run against the web), what's implemented but needs your
GPU/Ollama to exercise, and what Phase 1 defers by design (browser pool,
Tier-2 NLI, vLLM tier, fleet, self-improvement).
