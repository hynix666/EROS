# EROS v3.2 — Build Status (honest ledger)

Phase coverage: **Phase 0 (infrastructure bootstrap) + Phase 1a/1b core**
of the §13 roadmap, on the trust chain end-to-end. Every claim below is
labeled like the canonical doc: [verified] ran here, [implemented]
written and unit-covered but needing hardware/network this sandbox lacks,
[deferred] intentionally out per the document's own constraints.

## Verified in this build (live execution)
- [verified] Canonical schema + trust-chain DDL applies clean,
  `ON_ERROR_STOP` semantics (EMBEDDING_DIM=1024).
- [verified] **Negative suite 15/15** — Gates 1–4, ADR-017 attested
  XFAM (same-family / unattested / unknown model), ADR-018 infeasible
  target, WORM ×3, g00 illegal transitions, Analyst privilege denial,
  promotion path, happy-path publish. Canonical §12 error strings exact.
- [verified] **DGK 21/21** — all eight adversarial variant classes of
  §6.8.1 (scale_unified, percent_vs_decimal, iso_date, dmy_vs_mdy,
  fuzzy_entity/ocr_noise, normalized_quote, paraphrase) in both grounded
  and must-prove-UNGROUNDED directions; canonical tolerances recorded.
- [verified] Graph-shape CI (C9): no re-entry into analyze; verify only
  from analyze; only the two bounded back-edges exist.
- [verified] Artifact write ordering: simulated crash between rename and
  commit leaves an orphan file and **no row**; re-execution idempotent.
- [verified] Governor: atomic reserve refuses at the ceiling (zero rows),
  settle refunds synchronously, idempotency key returns the prior
  reservation, startup reconciliation releases/orphans per §6.4.
- [verified] FR9 honest termination (insufficient_evidence observed live)
  and a **full end-to-end published run** against the live web:
  Wikipedia → fsync-ordered store → 50 chunks → extractive drafts →
  DGK verification → Gate-3 ledger (coverage 1.0 by construction) →
  publication through the real g00/g10/g20/g30 triggers.
- [verified] LIL API + built frontend served from one process: /health,
  /runs, run detail + telemetry, ledger-assembled Markdown report,
  evidence browser API, KG assist endpoints (deterministic fallback),
  FR18 409 contract, C10 durable queue path, human-gate approve path.
- [verified] Runtime role scoping: pipeline writes execute under
  SET LOCAL ROLE (Analyst→staging only; Verifier sole claim writer;
  Reporter ledger; Ingestor stores).
- [verified] Startup-as-recovery: schema assert, budget reconcile,
  orphan sweep, RESTART_RECOVERED audit row.

## Implemented, not verifiable in this sandbox
- [implemented] Slot Ledger evict-then-load under `pg_advisory_xact_lock`
  with /api/ps reconciliation and divergence paging — no GPU/Ollama here;
  logic unit-structured, network paths untested.
- [implemented] Deep GGUF attestation (tag→blob digest resolution +
  streaming SHA-256) and `attest_models.py --pin` — needs a real Ollama
  store.
- [implemented] Model-mode pipeline paths (drafter JSON claims, checker
  cross-family supports/contradicts with contest_strength, arbiter
  adjudication, judge QA scoring) — exercised only in their deterministic
  fallbacks here.
- [implemented] External escalation with ZDR admission, pre-dispatch
  idempotency, settled-response re-read, orphan-on-crash — no keys here;
  OFF by default.
- [implemented] MCP stdio server (initialize/tools list/call;
  MCP_SENSITIVE_RUN_INITIATED audit).

## Deferred — per the document's own constraints
- vLLM + LMCache tiered KV (C5 / ADR-002 flip unmet)
- Multi-agent fleet (C6), self-improvement loops (C7)
- Qdrant / Neo4j (C4; flip conditions in ADR-003)
- Sandboxed Playwright browser pool (FR2 JS-heavy pages): the `browser`
  extra is declared; the pool, Firejail profile, and timeout ladder are
  not wired into the Phase-1 pipeline.
- Tier-2 NLI advisory head (M5 measurement gate), Oracle Gold Set
  seeding tooling (table + constraints exist), EWMA drift detector,
  Prometheus/Grafana/Langfuse wiring, restic replication job, XFS
  project quotas (host-level; watermark checks are in-process).
- Windows directory-fsync is unavailable at the OS level; the store
  fsyncs file bytes and uses atomic `os.replace` there — disclosed in
  the module and README, not papered over.

## Known honest limitations of deterministic mode
Without local models, the Analyst is extractive and verification is
kernel-only (`verification_kind='deterministic'`); relevance depends on
FTS ranking (install the `embeddings` extra for hybrid retrieval). Every
such degradation is disclosed verbatim in the report footer — that is
the design working, not failing.
