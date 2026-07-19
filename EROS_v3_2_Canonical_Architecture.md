# EROS — Canonical Architecture Design Document

**Status:** Canonical — Conditionally Frozen  
**Version:** 3.2 (Definitive Unified)  
**Date:** 2026-07-17  
**Scope:** Phase 1 "Core Loop" — single-node, local-first, autonomous research pipeline with a database-enforced trust chain and a universal interface layer.  
**Deployment Target:** Single AMD workstation — Ryzen 9 9950X3D (Zen 5, native AVX-512 + VNNI, dual CCD, 3D V-Cache on CCD0) · Radeon RX 9070 XT 16 GB (ROCm — never CUDA) · 96 GB DDR5 · Kingston KC3000 2 TB NVMe · plus one NAS replication target.

---

## 0. Governance & Freeze Rule

### 0.1 Executable Specification Mandate

No DDL, migration, transition table, or numeric threshold enters this document, or any future amendment to it, until it has executed against the pinned PostgreSQL version in CI with `ON_ERROR_STOP=1`, and every gate claimed to hold has a negative test that *attacks* it and is shown to fail closed. A schema that has never been run is a design sketch wearing a schema's clothes; a threshold that has never been checked for feasibility is a wish with a comparison operator. `make verify-spec` extracts every fenced `sql` block from this document and applies it to a scratch database; the build fails on any error.

### 0.2 Conditional Freeze

This document is **conditionally frozen**: closed to new features, open to measurement. It becomes fully frozen once M1–M12 (Appendix D) return. Until then, every unmeasured number carries an explicit `[assumption]` tag, and **no `[assumption]` may appear in a table of pass/fail release gates.**

Amendments require an ADR: **Context** (the specific problem) · **Options** (≥2 real alternatives, including "do nothing") · **Flip conditions** (exact metrics that trigger adoption and rollback) · **Consequences** · **Rejection rationale.** An ADR without flip conditions is dogma with a date on it. Work with no real alternative to weigh (tuning autovacuum, writing a runbook) is a ticket, not an ADR.

Every claim in this document is labeled **[verified]** (measured or executed), **[judgment]** (engineering inference), or **[assumption]** (unvalidated premise, listed in Appendix D). An assumption is never allowed to read as a fact.

---

## 1. Executive Summary

EROS is the **Core Loop** of an Enterprise Research Operating System: a single autonomous pipeline that takes a natural-language research question, performs deep multi-source research, stores every retrieved artifact with full provenance, verifies every claim against evidence using a model of a **different base lineage** than the one that drafted it, and produces a report in which every sentence resolves to a verified claim, a whitelisted template, or a disclosed synthesis label — never to nothing.

### 1.1 The seven decisions that shape everything else

1. **Workflow-engine control (ADR-001).** LangGraph with Postgres checkpointing, not agents calling agents. Traceability, bounded loops, and resumability from day one.
2. **Single datastore (ADR-003).** PostgreSQL (relational + pgvector + FTS), not a multi-store constellation. Minimum moving parts, with measured flip conditions for graduating any single store.
3. **Cross-family verification with runtime attestation (ADR-005, superseded by ADR-017 for attestation enforcement).** The Checker's base lineage differs from the Drafter's, enforced by the Router *and* backed by SHA-256 attestation of the resident weights, *and* backed by a database privilege boundary that makes the Analyst structurally unable to write verified evidence.
4. **The Sequential Slot Ledger (ADR-009).** VRAM is a governed resource with deterministic occupancy, not a saturated one. At most two models resident at any instant: one **Generation Slot** (Drafter XOR Checker), one **On-Demand Slot** (Arbiter XOR Judge).
5. **Crash-consistency as a first-class contract (ADR-010).** Every node is re-executed after a crash and is therefore idempotent or compensating; startup is *always* treated as a recovery event; checkpoints fail closed rather than defaulting trust telemetry.
6. **The trust chain is welded in the database (ADR-012–ADR-019).** A claim cannot exist without evidence, a run cannot publish without verified evidence, a report sentence cannot exist without naming a real claim or a disclosed synthesis label, and a claim cannot survive publication if a deterministic kernel proves its numbers, dates, entities, or quotations do not occur in its cited evidence.
7. **The Universal Interface Layer (LIL) (ADR-021).** All agents, loops, memory systems, and humans interact through a single local API and event bus. This boundary enforces evidence capture, budget metering, and isolation from day one, providing the structural seams for Phase 2 cognitive memory and Phase 3 self-improvement without rewriting the core.

---

## 2. Capability Map & Phased Vision

### 2.1 Eight planes, ~35 domains

- **A — Research Execution:** autonomous research/planning, search intelligence, data collection, document intelligence, multimedia intelligence.
- **B — Knowledge & Memory:** knowledge graph/ontology, memory architecture (working/episodic/semantic/procedural), evidence management (every claim carries source, confidence, freshness, trust score).
- **C — Reasoning & Analysis:** fact verification, advanced reasoning, analytics, forecasting, decision support, risk intelligence.
- **D — Continuity:** continuous monitoring, alerting.
- **E — Delivery:** reporting, visualization, collaboration, human-in-the-loop.
- **F — Trust & Enterprise:** security (RBAC/ABAC/Zero Trust), compliance, research quality scoring.
- **G — Platform & Infrastructure:** integrations, API platform, AI model layer, ML, observability, evaluation, infrastructure, DevOps, cost management (Heuristic Gate).
- **H — Growth:** domain packs, extensibility, self-improvement (always gated).

### 2.2 Phased roadmap — never attempt all planes concurrently

| Phase | Objective | Exit criterion |
|---|---|---|
| **1 — Core Loop** *(this document)* | Single-agent reliability | One agent produces a sourced brief, every claim traceable, on local inference alone, with the trust chain database-enforced |
| **2 — Platform & Memory** | Stateful workflows, structured knowledge, tiered KV | Knowledge persists and improves across sessions (`memory.*` H-MEM/GAM active); contradictions are detected; vLLM+LMCache tiered KV active |
| **3 — Engine & Self-Improvement** | Scale, routing, cost control, bounded harness optimization | Router demonstrably cuts cost/latency; `harness.*` plane active (ACE online, AHE offline) with promotion gates holding under audit |
| **4 — OS** | Full multi-agent enterprise integration | Third parties add agents/packs without core changes; multi-agent fleet deployed with conflict-resolution protocol battle-tested; `memory_sync_shimi` for second machine |

---

## 3. Assumptions, Constraints & Deployment Boundary

### 3.1 Assumptions

| ID | Assumption | Label | Impact if violated | Mitigation |
|:---|:---|:---|:---|:---|
| A1 | Single primary user; multi-tenant isolation deferred. | `[assumption]` | Low | RBAC arrives with collaboration in Phase 4 |
| A2 | RX 9070 XT runs under ROCm with a supported Ollama build | `[assumption]` — **highest schedule risk** | High | Validate Day 1 of Phase 0. On failure: explicitly downgrade Drafter to **Qwen-2.5-1.5B-Instruct** at 8k context, prefer external escalation, apply degraded CPU partition. User is presented with a "fast-abort" choice; **never a silent default.** |
| A3 | Internet access is typical; air-gapped is a supported degraded state | `[assumption]` | Medium | Local-only fallback; no external search or escalation |
| A4 | Anthropic + OpenAI keys exist with monthly budget caps; default $50/month | `[assumption]` | Low | Governor halts on breach; resumable. ZDR required for admission. |
| A5 | DRAM is 96 GB | `[verified]` | Low | Degradation, not failure |
| A6 | A NAS replication target is reachable | `[assumption]` | Medium | Replication degrades to nightly local-drive copy with disclosed, worse RPO. `restic` failure warns, never blocks. |
| A7 | KV-cache size and model-load-latency figures underlying the Slot Ledger are estimates | `[assumption]` — **M1–M4** | Medium | All four are Phase 0 deliverables; documented degradation levers apply |
| A8 | Tier-2 NLI cross-encoder (~180M params, int8) runs ≤30ms/pair on AVX-512 VNNI | `[assumption]` — **M5** | Low | Demotes to nightly batch; Gate 4 (blocking) is unaffected |
| A9 | Per-claim Judge/Oracle disagreement σ ≈ 0.25 | `[assumption]` — **M6** | Low | Re-derive Oracle sample-size floor from measured σ |
| A10 | Gate score distribution is approximately binormal, equal variance | `[judgment]` | Low | Replace closed-form threshold with empirical ROC sweep |
| A11 | DGK false-UNGROUNDED rate on faithful paraphrase < 2% | `[assumption]` — **M7** | **High if wrong** | Gate 4 runs in shadow mode until tuned; appeal is to human, never a model |
| A12 | Arbiter family selection requires a measured ladder, not an asserted default | `[assumption]` | Medium | Resolved by Phase 0 Day 3 (ADR-011.1) |
| A13 | vLLM ≥ v0.20.0 is the version floor for the FP8/RDNA4 path | `[assumption]` | Medium | Flagged path remains OFF until `check-pin-prs` and `check-fp8-kernels` pass |
| A14 | NVMe tier has no direct GPU access; GPU↔NVMe transfers stage through DRAM | `[verified]` | Low | Latency budgets and diagrams reflect this topology |

### 3.2 Constraints

- **C1:** Content labeled `sensitive` never leaves the machine.
- **C2:** No Kubernetes.
- **C3:** No Temporal (redundant with Postgres checkpointing at single-node scale).
- **C4:** No Qdrant/Neo4j (flip conditions in ADR-003).
- **C5:** No vLLM/LMCache in Phase 1 (no measured context exhaustion; ADR-002).
- **C6:** No multi-agent fleet (pipeline stages first).
- **C7:** No self-improvement loops in Phase 1 (no recursive self-modification).
- **C8:** No more than two models resident in VRAM at any instant — enforced at infrastructure layer (`OLLAMA_MAX_LOADED_MODELS=2`) and via Postgres advisory locks.
- **C9:** The research graph is phase-batched. Per-claim interleaving of Analyst and Verifier is prohibited and asserted in CI. **C8 depends on C9.**
- **C10:** One active run. The GPU slot mutex, budget reservation model, and CPU partition all assume this.
- **C11:** Trust never influences retrieval. `trust_seed` does not weight ranking — ranking is vector similarity + BM25 + reranker score, full stop. **`trust_seed` is stored strictly for audit and provenance tracking; it exerts zero influence over retrieval, ranking, or verification logic.**
- **C12:** Cognitive memory and KV-cache are strictly separated. `memory.*` does not own VRAM; `kvcache.*` does not leak into retrieval logic.

### 3.3 Deployment boundary

The system is **single-node plus one replication target.** The NAS holds no state the system reads during operation — it is not a component — but it is a dependency of the disaster-recovery guarantee (FR7). **Compute, state, and inference are single-node. Durability is not.**

---

## 4. Requirements

### 4.1 Functional Requirements

| ID | Requirement | Priority |
|:---|:---|:---|
| FR1 | End-to-end autonomous research loop with optional human gates | P0 |
| FR2 | Deep internet search across engines, academic/news verticals, and JS-heavy pages via headless browser | P0 |
| FR3 | Evidence management with full provenance (source, URL, doc ID, locator, timestamps, hashes, confidence, trust score) | P0 |
| FR4 | Fact verification via citation validity, multi-source cross-check, contradiction detection; contested claims surfaced as explicit uncertainty | P0 |
| FR5 | Model routing: local Ollama by default; external escalation only when sensitivity and budget allow, and only to ZDR providers | P0 |
| FR6 | Report generation in Markdown with HTML/PDF export; **every sentence resolves to a verified claim, a whitelisted template, or a disclosed synthesis label** (Gate 3) | P0 |
| FR7 | Pause/resume via checkpointing across process restarts. "Never lost work" covers the evidence itself, not merely pointers | P0 |
| FR8 | Local search over ingested corpus to avoid re-crawling | P1 |
| FR9 | A run that cannot find sufficient evidence terminates saying so; it does not synthesize | P0 |
| FR10 | A run is cancellable cleanly at any node boundary | P1 |
| FR11 | Every store operates under an enforced quota; quota exhaustion degrades visibly and never starves a neighbouring store | P0 |
| FR12 | The system shuts down and restarts cleanly during an active run — no orphaned processes, no leaked VRAM, no stranded budget reservations | P0 |
| FR13 | Every workflow node is idempotent or provides compensating recovery; a resumed run never double-spends and never double-writes | P0 |
| FR14 | **A claim whose numbers, dates, entities, or quotations do not occur in its cited evidence cannot be published, proven by a kernel in which no model participates** (Gate 4) | P0 |
| FR15 | **A verification cannot be labelled cross-family unless the models are genuinely different attested families; the Analyst is structurally unable to write verified evidence** | P0 |
| FR16 | Every task runs a bounded lifecycle (Plan → Execute → Evaluate → Reflect → Update Memory → Schedule) with a machine-checkable success condition and hard ceiling | P0 |
| FR17 | Hard deletion of cognitive memory is an irreversible action requiring a human approval gate | P1 |
| FR18 | If ROCm is unavailable, the system must prompt the user with an explicit "fast-abort" choice rather than silently defaulting to a 3-hour CPU fallback | P0 |

### 4.2 Non-Functional Requirements

| Target | Value | Notes |
|---|---|---|
| Report turnaround | ≤ 15 min p50 · ≤ 45 min p95 | Bounded by search + verification, not inference |
| Interactive latency | ≤ 5 s p50 first token | Local Ollama |
| **Citation coverage** | **= 1.0, exactly, by construction** | Gate 3. Any other measured value is a bug in Gate 3 |
| Groundedness (QA-Eval, sampled) | ≥ 0.9 | Advisory layer above blocking Gate 4 |
| **DGK false-UNGROUNDED rate** | **< 2% on the Gold Set (M7)** | Below threshold → Gate 4 arms; above → shadow mode |
| VRAM budget | ≤ 13.5 GB of 16 GB steady state | Worst case ~9.0 GB under Slot Ledger |
| Model loads per run | p95 ≤ 5 | Exceeding means graph drifted out of phase-batched shape |
| External API spend | Hard ceiling, $50/month | Governor-enforced, atomic + owned reservation, checkpoint-pause |
| CPU-fallback turnaround | ≤ 180 min p95 (4×) | Formally relaxed, disclosed, and gated by FR18 |
| Sensitivity guarantee | `sensitive` content never leaves the machine | Hard Router constraint + audit + ZDR admission |
| Sensitivity classifier recall | ≥ 0.98 on adversarial corpus | A leaky classifier still leaks downstream |
| Gate escalation rate | ≤ ρ = min(0.35, ρ_budget) | ρ_budget derived from $50/mo ceiling |
| System RPO | < 1 hour, database *and* object store | DB restored from WAL is worthless if `snapshot_path` dangles |
| Availability | Crash = resume, never lost work | Checkpointing + hourly artifact replication |

---
## 5. High-Level Architecture

### 5.1 System architecture

```text
┌──────────────────────────────────────────────────────────────────────┐  
│ UI LAYER             Web UI (local): research workspace · evidence   │  
│                      browser · run monitor · report viewer   + CLI   │  
├──────────────────────────────────────────────────────────────────────┤  
│ API GATEWAY / LIL    FastAPI: REST + WebSocket · MCP server ·        │  
│                      local authn · correlation IDs · durable queue   │  
│                      [LIL: Sync API + Async Event Bus boundary]      │  
├──────────────────────────────────────────────────────────────────────┤  
│ ORCHESTRATION        LangGraph workflow (typed state, conditional    │  
│                      edges, Postgres checkpointer) · HEURISTIC GATE ·│  
│                      BUDGET GOVERNOR (owned reservation ledger) ·    │  
│                      HUMAN GATE · LIFECYCLE MANAGER                  │
│                      [LIL: loop.* 6-step lifecycle, Two-Stops]       │  
├──────────────────────────────────────────────────────────────────────┤  
│ PIPELINE STAGES      Planner → Searcher → Ingestor → Retriever →     │  
│ (workflow nodes,     [EVIDENCE SUFFICIENCY GATE] → Analyst →         │  
│  not free agents)    Verifier → [Arbiter] → Reporter → QA-Eval       │  
│                      PHASE-BATCHED (C9) · EVERY NODE IDEMPOTENT      │
│                      [LIL: agent.spawn/delegate/verify]              │  
├──────────────────────────────────────────────────────────────────────┤  
│ TRUST CHAIN (DB)     GATE 1 evidence-required (NOT NULL FK) →        │
│                      ATTESTED CROSS-FAMILY CONSTRAINT →              │
│                      GATE 4 Deterministic Groundedness Kernel        │  
│                      (blocking, no model) + Tier-2 NLI (advisory) →  │
│                      GATE 3 report ledger (coverage = 1.0) →         │
│                      GATE 2 publish requires verified evidence       │  
├──────────────────────────────────────────────────────────────────────┤  
│ AI INFERENCE         MODEL ROUTER + SEQUENTIAL SLOT LEDGER           │  
│                      ├─ Generation Slot:  Drafter XOR Checker        │  
│                      └─ On-Demand Slot:   Arbiter XOR Judge          │  
│                      ──► Ollama/ROCm ──► llama.cpp CPU ──►           │  
│                          Anthropic ──► OpenAI (ZDR + budget gated)   │  
│                      CPU: embeddings + reranker (AVX-512 VNNI) ·     │  
│                           unified CPU Classifier Service ·           │  
│                           Tier-2 NLI entailment head                 │
│                      [LIL: model.infer() & kvcache.* tiering hints]  │  
├──────────────────────────────────────────────────────────────────────┤  
│ KNOWLEDGE & DATA     PostgreSQL 16 — the ONLY transactional store:   │  
│                      runs · checkpoints · run_queue · artifacts ·    │  
│                      chunks (pgvector HNSW + FTS) · claims ·         │  
│                      claim_evidence · report_sentences ·             │  
│                      groundedness_kernel_results · oracle_gold_set · │  
│                      budgets + budget_reservations · approvals ·     │  
│                      degraded_mode_log · events · audit (WORM)       │  
│                      ── the ONE non-transactional boundary: ──       │  
│                      OBJECT STORE /data/artifacts (NVMe)             │  
│                         fsync → rename → THEN commit the row         │  
│                         → hourly restic → NAS                        │  
├──────────────────────────────────────────────────────────────────────┤  
│ SEARCH & COLLECT     Connector framework · sandboxed Playwright pool │  
│                      · polite crawler (robots.txt, per-host limits)  │  
├──────────────────────────────────────────────────────────────────────┤  
│ SECURITY             age/keyring vault · sensitivity policy engine · │  
│                      WORM audit · ZDR egress admission · immutable   │  
│                      trust configuration · role-separated DB grants  │  
├──────────────────────────────────────────────────────────────────────┤  
│ INFRA/OBSERVABILITY  OTel → Prometheus + Grafana (bounded cardinality)│  
│                      · Langfuse (self-hosted, REFERENCE-ONLY spans) ·│  
│                      SLOs                                            │  
└──────────────────────────────────────────────────────────────────────┘  
```  

All local processes/containers via Docker Compose. Kubernetes explicitly excluded (C2).

### 5.2 Trust-layer flow  

```text
User → Gateway + LIL      [auth · correlation ID · sensitivity boundary]  
     → Heuristic Gate       [rules → CPU classifier · derived-target release gate]  
     → Workflow             [typed state · bounded loops · idempotent nodes · phase-batched]  
     → Model Router         [SENSITIVITY hard constraint, evaluated FIRST]  
     → Slot Ledger          [≤2 resident · evict-then-load · pg_advisory_xact_lock · /api/ps reconciled]  
     → Lineage Registry     [Drafter ≠ Checker ≠ Arbiter · SHA-256 attested at startup + periodically]  
     → Context Ceiling      [12k/8k baked into derived Modelfiles; base tags blocked]  
     → Claim Sensitivity    [max(evidence sensitivities) · per-claim routing, audited]  
     → Evidence Gate        [≥3 chunks, or bounded re-plan, or terminate honestly]  
     → GATE 1 (evidence)    [DB NOT NULL FK: a claim with no primary evidence cannot commit]  
     → ATTESTED XFAM CHECK  [DB trigger: a check cannot be labelled cross-family unless it genuinely is]  
     → Verifier + Arbiter   [cross-family, DB-privilege-enforced · contested surfaced, never hidden]  
     → GATE 4 (DGK)         [blocking, no model participates · Tier-2 NLI advisory only]  
     → GATE 3 (report ledger)[every sentence: verified claim | template | disclosed synthesis]  
     → GATE 2 (publish)     [DB constraint: publish refused if any claim lacks verified evidence]  
     → QA-Eval               [sampled groundedness ≥0.9 · advisory quality layer above Gate 4]  
     → WORM Audit            [UPDATE/DELETE/TRUNCATE rejected]  
     → Report                [per-sentence provenance · every degradation disclosed in the footer]  
```  

**Every arrow fails closed. No arrow degrades silently.** Gates 1–4 are database constraints and triggers, not prompts or Router promises.

---

## 6. Component Specifications

### 6.1 Gateway & Universal Interface Layer (LIL)

- **Responsibility:** Single entry point (REST/WS/MCP), auth, correlation-ID minting, routing to Gate and workflow. The LIL exposes a **Sync API** (FastAPI endpoints) and an **Async Event Bus** (Postgres `LISTEN/NOTIFY` + `events` table).
- **LIL Boundary Enforcement:** No agent or pipeline node touches the store, serving engines, or another agent's state except via LIL methods or bus events. This makes evidence capture, budget metering, and ownership checks total rather than best-effort. The core interfaces are defined in **ADR-021**.
- **LIL Interface Contract:**

| Interface | Transport | Method / Event | Input | Output | Owner |
|---|---|---|---|---|---|
| `agent.spawn` | Sync API | `POST /lil/agent/spawn` | `{task_type, prompt, envelope}` | `{agent_id, status}` | Orchestration |
| `agent.delegate` | Sync API | `POST /lil/agent/delegate` | `{agent_id, subtask}` | `{result, evidence_refs}` | Orchestration |
| `agent.verify` | Sync API | `POST /lil/agent/verify` | `{claim_id, evidence_refs}` | `{verdict, confidence}` | Verifier |
| `model.infer` | Sync API | `POST /lil/model/infer` | `{task_type, prompt, lineage_req}` | `{completion, tokens, latency}` | Router |
| `memory.read` | Sync API | `GET /lil/memory/read` | `{scope, query, limit}` | `{events[]}` | Memory |
| `memory.write` | Async Bus | `memory.write` event | `{scope, payload}` | — | Memory |
| `kvcache.hint` | Sync API | `POST /lil/kvcache/hint` | `{model, tier_preference}` | `{slot_assignment}` | Router |
| `budget.reserve` | Sync API | `POST /lil/budget/reserve` | `{run_id, node, estimate}` | `{reservation_id, status}` | Governor |
| `budget.release` | Sync API | `POST /lil/budget/release` | `{reservation_id, actual}` | `{refund}` | Governor |
| `evidence.log` | Async Bus | `evidence.ingested` event | `{artifact_id, chunk_ids[]}` | — | Ingestor |

- **MCP Server Contract:** `stdio` transport. Tools: `research_start`, `research_status`, `evidence_query`. JSON Schema Draft 2020-12. Sensitivity enforced at tool boundary; `MCP_SENSITIVE_RUN_INITIATED` audited.
- **Concurrency:** One active research run at a time (C10). A `POST /research` while a run is active returns `RunQueued`. The queue is persisted to `run_queue` (Dequeue: `SELECT … FOR UPDATE SKIP LOCKED`).
- **Cancellation:** `DELETE /research/{id}` sets `cancel_requested`. The workflow checks the flag at every node boundary and performs cooperative shutdown: release VRAM slots, release unspent budget, kill browser tasks, write terminal checkpoint.
- **Degraded Mode UI Interaction (FR18):** If ROCm is unavailable at run start, the LIL does not silently queue a 3-hour CPU fallback. It returns `DegradedModeDetected` to the UI/CLI, requiring the user to explicitly select "Proceed in Degraded Mode (est. 180m)" or "Abort".

### 6.2 Heuristic Gate

- **Responsibility:** Classify each request *before* expensive machinery starts and attach the initial budget envelope. Mandatory.
- **Tech:** Rules fast-path + local classifier (Phi-4-mini in the unified CPU Classifier Service). Confidence < 0.7 → default to `full_investigation`.
- **Drift Handling:** `gate_accuracy` computed post-hoc by QA-Eval. Operational recalibration: accuracy < 0.85 over 20 runs → retune. Architectural flip: accuracy < 0.80 after 3 prompt iterations → ADR.
- **Gate Economics (ADR-018):** The release gate is derived from the classifier's measured ROC curve, never asserted. `derived_target = 1.15 × min_t C(t; d̂, π̂)`. An escalation ceiling `ρ = min(0.35, ρ_budget)` binds the gate to the $50/mo budget. The database refuses to store an infeasible target (`CHECK (derived_target >= achievable_cost)`).
- **In-Session Escalation:** A bounded, single-step promotion checked at three triggers: (1) Planner used both re-plans and avg evidence sufficiency < threshold; (2) >25% of load-bearing claims are `contested`; (3) >90% envelope consumed **and** average `claim_evidence.confidence` < 0.70. At most one escalation per run. Fresh envelope for remaining work only. Disclosed verbatim in report footer.

### 6.3 Research Workflow & Loop Engine (LangGraph)

- **Responsibility:** Owns *all* control flow — typed state schema, conditional edges, per-node retry policies, bounded revision loops, optional human-approval node, Postgres checkpointer.
- **Graph Shape (C9):** Phase-batched. Analyst drafts *all* claims before Verifier verifies *any*; Verifier verifies *all* before Arbiter adjudicates *any*. Per-claim interleaving is prohibited (asserted in CI).
- **Loop Engine (LIL `loop.*`):** Every task adheres to the 6-step lifecycle: **Plan → Execute → Evaluate → Reflect → Update Memory → Schedule**, with the Two-Stops rule (machine-checkable success condition + hard ceiling). Mixed tasks use open-localize → closed-land two-phase templates.
- **RunState v4:** Uses UUID references (never embedded objects) for sources, artifacts, and claims to prevent checkpoint bloat. Tracks `lineage_attestation_status`, `slot_transitions`, `provenance` digest (config/git/image/model digests). Trust telemetry fields are never defaulted by a migration (ADR-010).
- **Human Gate:** 24h timeout. On expiry, run pauses (`paused_approval`). **Approval state is durable in the `approvals` table.** While open, the run releases VRAM and budget. Desktop notification + UI banner emitted.

### 6.4 Budget Governor

- **Policy:** Default **$50/month** external ceiling. Local inference is free (tracked, not gated).
- **Owned Reservation Ledger:** Reservations live in `budget_reservations` with `run_id`, `node_name`, `idempotency_key`, and `estimated_cost`. Under `SERIALIZABLE` isolation, two concurrent reservations against a shared ceiling cannot breach it.
- **Atomic Reserve:** Synchronous, one statement. Zero rows returned = reservation refused.
- **Synchronous Release:** On receipt of the API response, the Governor releases `(estimated − actual)` back to the pool **in the same transaction** that records actual spend.
- **Reconciliation at Startup:** Rows still `reserved` are released if `provider_request_id IS NULL` (never dispatched) or `orphaned` if present (possibly billed, operator alerted).

### 6.5 Pipeline Nodes & Agent Roles (LIL `agent.*`)

Nodes never name a model; they request inference by **task type**. Roles are cheap (prompt+tools+playbook bundles); model instances are scarce.

- **Planner:** Decomposes question into task list. At most two re-plans.
- **Searcher:** Fans out via Connector Framework. Emits `SourceDiscovered`.
- **Browser Pool:** Playwright, bounded pool of 3. Sandboxed (Firejail/seccomp-bpf). Timeout ladder: navigation 30s → DOM-ready 10s → total 60s. On timeout: `SIGKILL` after 5s grace. 2 GB RSS cap per worker.
- **Ingestor (`eros_ingestor` — Evidence Store owner, single writer):** Pipeline: fetch → parse → chunk → embed → classify sensitivity → **write**. Write order is load-bearing: `fsync` file → `fsync` dir → `rename()` → **THEN commit** the row. Idempotent by content address (`artifacts.hash` UNIQUE).
- **Embedding + Reranker Service (CPU):** `bge-large-en-v1.5` embedder + cross-encoder reranker, int8, on AVX-512 VNNI path. Pinned to V-Cache CCD. 256-chunk batches. Keeps GPU free for generation.
- **CPU Classifier Service:** One Phi-4-mini process serving both Heuristic Gate and sensitivity classification. Pinned to cores 8–11.
- **Retriever:** Hybrid pgvector HNSW + Postgres FTS/BM25, fused by reciprocal-rank fusion, then CPU rerank. **Evidence Sufficiency Gate:** asserts ≥3 chunks above threshold or triggers bounded re-plan / `insufficient_evidence` terminal status.
- **Analyst (`eros_analyst`):** Drafts claims from retrieved evidence. Output parsed through Pydantic. **Writes to `draft_claim_evidence` only.** The claim, its `primary_evidence_chunk_id`, and its `confidence` score are staged; promotion to `claims` is performed by the Verifier or an automated promotion node under the Verifier's authority.
- **Verifier (`eros_verifier`):** Citation validity, multi-source cross-check, contradiction detection. Sole writer to `claim_evidence`. `verification_kind` computed with strict-priority aggregation (any `same-family` check makes the claim `same-family`). **Contested claims are recorded with both chains and a `contest_strength` score (0.0–1.0) derived from the confidence delta between supporting and contradicting evidence.** Adjudicated in one Arbiter session.
- **Oracle (Offline Evaluator):** A held-out, high-capacity reference model (e.g., GPT-4-class) used strictly offline for Gold Set calibration. It evaluates claim groundedness to establish `σ(judge − oracle)`. It is **not** deployed in the runtime pipeline, consumes no runtime budget, and cannot block a run.
- **Reporter (`eros_reporter`):** Assembles from `verified` + `contested` claims only. Writes the **Report Ledger** (Gate 3). No inference. Footer discloses every degradation.
- **QA-Eval:** Samples sentences for groundedness using the deployed Judge (On-Demand Slot). Below threshold → one bounded revision → else human gate.

#### 6.5.1 Search & Ingestion Specification
- **Search Engines:** API-first connectors (Bing, OpenAlex, Crossref). No direct HTML scraping without API fallback.
- **Authentication:** Connectors support API-key auth (header or query param) and OAuth 2.0 client credentials. Paywalled sources (e.g., academic databases) require operator-configured credentials in the age vault; **sensitive content never routes through external auth without ZDR verification.**
- **Politeness:** 1 request/sec/host. `robots.txt` respected.
- **Non-HTML Handling:**
  - **PDF:** `pdfplumber` for text extraction; `PyMuPDF` for image extraction. Images are OCR'd via `tesseract` if text layer is absent.
  - **Images:** Stored as artifacts with `content_type = 'image/*'`; downsampled to WebP at 30 days.
  - **Video/Audio:** Phase 1 not supported. Trigger: Phase 2 ADR if multimedia queries recur.
- **Chunking Strategy:** Recursive character splitter with 512-token target size and 20% overlap (102 tokens).
- **Multi-Language:** English-only for Phase 1 (bge-large-en-v1.5). Multi-language triggers Phase 2 ADR.
- **Connector Abstraction:**
  ```python
  class Connector(ABC):
      @abstractmethod
      def search(self, query: str, max_results: int) -> List[Source]: ...
      @abstractmethod
      def fetch(self, url: str) -> Artifact: ...
      @property
      @abstractmethod
      def rate_limit(self) -> RateLimit: ...
  ```
  New connectors are registered by implementing this interface and adding an entry to `connectors.yaml`.

### 6.6 Model Router, Slot Ledger & Context Ceiling

- **Router Rule Order:** **Sensitivity → Lineage → Slot Availability → Task/Cost/Latency/Health**. Sensitivity is a hard constraint evaluated first. Lineage is fail-closed at config time; fail-open-with-labeling at runtime.
- **Fallback Chain:** Ollama primary → Ollama secondary → llama.cpp CPU → (if permitted) Anthropic → OpenAI. External providers must offer Zero Data Retention (ZDR).
- **Lineage Registry & Versioning (ADR-022):** Drafter (Llama-3.x), Checker (Qwen-2.x), Arbiter (≤4B, measured ladder ADR-011), Judge (Phi-4). Models are pinned via a `model_version` manifest (`/data/models/manifest.json`) containing the semantic version and the SHA-256 GGUF digest. Routine updates require an operator to update the manifest and the attestation checksum simultaneously via an audited ADR process.

**Manifest Schema:**
```json
{
  "version": "3.2.0",
  "updated_at": "2026-07-17T12:00:00Z",
  "updated_by": "operator",
  "adr_ref": "ADR-022",
  "models": {
    "drafter": {
      "family": "llama3",
      "variant": "llama-3.1-8b-instruct",
      "quantization": "q8_0",
      "num_ctx": 12288,
      "gguf_digest": "sha256:abc123...",
      "modelfile_tag": "eros-drafter-12k"
    },
    "checker": { ... },
    "arbiter": { ... },
    "judge": { ... }
  }
}
```

**Flip Conditions:**
- **Adopt:** Manifest schema validates against JSON Schema Draft 2020-12 in CI.
- **Rollback:** If attestation mismatch rate > 0.1% due to manifest drift, revert to digest-only pinning.

- **Runtime Attestation:** Digest pinned to GGUF weight blob. Startup, periodic (20 runs/24h), and resumption re-attestation. Mismatch → fail-closed.
- **Sequential Slot Ledger (ADR-009):** Exactly two slots. **Generation Slot** (Drafter XOR Checker), **On-Demand Slot** (Arbiter XOR Judge). `OLLAMA_MAX_LOADED_MODELS=2`. 
- **GPU Mutex:** Before issuing any evict-then-load command, the Router acquires a Postgres advisory lock (`pg_advisory_xact_lock`). This guarantees that even if the workflow engine races, the GPU mutex is strictly enforced at the application level. Reconciled against `GET /api/ps` before and after every transition. Rebuilt from `/api/ps` at startup, never from checkpoint.
- **Context Ceiling:** Enforced at load time via derived Modelfiles (`eros-drafter-12k`, etc.). Base tags blocked. Never silent truncation.
- **KV-Cache Tiering (LIL `kvcache.*`):** In Phase 1, Ollama handles KV cache natively. In Phase 2, a feature-flagged vLLM ≥0.20.0 + LMCache MP sidecar will provide tiered offloading (VRAM → DRAM → NVMe, staged via DRAM). LIL observes and hints; it does not reimplement tiering.

### 6.7 Cognitive Memory & Event Bus

- **Event Bus (Phase 1):** Postgres `LISTEN/NOTIFY` + `events` table. Published from the same transaction that writes the underlying row. `NOTIFY` carries an 8000-byte payload limit; payloads are fetched from `events` by ID. Missed notifications are UI staleness issues, never data loss. **The `events` table stores structured telemetry (`latency_ms`, `token_count`, `model_name`, `cost_estimate`) to support post-mortem analysis without relying on traces.**
- **Cognitive Memory (LIL `memory.*` — Phase 2+):** The `events` table acts as the flat episodic log for Phase 1. In Phase 2, this evolves into a hierarchical structure (H-MEM indexes, GAM consolidation, EM-LLM segmentation). Cognitive memory's hottest tier is DRAM-resident indexes and inclusion in the assembled prompt; it does not own VRAM.

### 6.8 Trust Chain & Database Invariants (Gates 1–4)

The trust chain is welded in the database. PostgreSQL fires `BEFORE ROW` triggers in alphabetical order, so trigger names are deliberately chosen to enforce cheapest/most-fundamental first:

1. **Gate 1 (Evidence Required):** Enforced structurally via a `NOT NULL` constraint on `claims.primary_evidence_chunk_id` **(ADR-020)**. The Analyst stages claims in `draft_claim_evidence`; promotion to `claims` is performed by the Verifier or an automated promotion node. This eliminates the deferred trigger scan at commit time.
2. **`g00_runs_status_transition`**: Is the run state transition legal?
3. **`g10_runs_publish_requires_verified_evidence` (Gate 2):** Every claim in the run carries VERIFIED evidence, or publish is refused.
4. **`g20_runs_publish_requires_grounded_claims` (Gate 4):** The Deterministic Groundedness Kernel (DGK) proves UNGROUNDED. No model participates. Tier-2 NLI entailment is advisory only.
5. **`g30_runs_publish_requires_report_provenance` (Gate 3):** Every sentence in `report_sentences` is `assertive` (names verified claim), `structural` (names template), `disclosure` (names template), or `labeled_synthesis` (requires disclosure line). Citation coverage becomes 1.0 by construction.

**Attested Verification Constraint (ADR-017):** A `BEFORE INSERT OR UPDATE` trigger on `claim_evidence` prevents labelling a check `cross-family` unless the models are genuinely different families AND the checker carries a live attestation. The Analyst role is structurally denied write access to `claim_evidence`.

**Role-Enforced Maker-Checker:** `eros_analyst` has `INSERT` on `draft_claim_evidence` only. `eros_verifier` is the sole writer of `claim_evidence`. The Analyst is structurally unable to write verified evidence.

#### 6.8.1 Deterministic Groundedness Kernel (DGK) Specification

The DGK proves UNGROUNDED. No model participates. It operates via pure deterministic text matching.

**Tokenisation & Normalisation Pipeline:**
1. **Text extraction:** Plaintext from chunk via `html-text` or `pdfplumber`.
2. **Sentence segmentation:** `nltk.sent_tokenize` or language-specific equivalent.
3. **Number normalisation:** Scale unification (e.g., "1.5 billion" → 1,500,000,000) with **0.5% rounding tolerance**.
4. **Date normalisation:** Parsed to ISO 8601 (`YYYY-MM-DD`) via `dateparser` strict mode; partial dates ("June 2024") normalised to first of month.
5. **Entity extraction:** Rule-based NER using **spaCy `en_core_web_sm`** + custom domain dictionaries. **0.25 Levenshtein tolerance** for OCR/parsing noise.
6. **Quotation extraction:** Exact substring match after `unicodedata.normalize('NFKC')` + whitespace collapse.
7. **Lemmatisation:** Not applied to numbers, dates, or quotations; applied to entity tokens only for fuzzy matching.

**Matching Logic:**
- **Numbers:** Normalized via scale unification (e.g., "1.5 billion" → 1,500,000,000) with a 0.5% rounding tolerance.
- **Dates:** Normalized to ISO 8601 format before exact matching.
- **Entities:** Rule-based NER + dictionary lookup with 0.25 fuzzy tolerance (Levenshtein distance) to account for OCR/parsing noise.
- **Quotations:** Exact substring matching after punctuation and whitespace normalization.

**Adversarial Gold Set Coverage (M7):**
The Gold Set must include variants for: scale-unified numbers ("1.5B" vs "1,500,000,000"), percent/decimal alternates ("10%" vs "0.1"), date format alternates ("June 5" vs "5 June" vs "2024-06-05"), entity OCR noise ("Micros0ft" vs "Microsoft"), and paraphrased quotations.

*If M7 (false-UNGROUNDED rate) measures ≥ 2% on the Gold Set, `entity_tolerance` is loosened (recorded in `groundedness_kernel_results.entity_tolerance`), or Gate 4 runs in shadow mode (record, do not block) until tuned.*

### 6.9 Crash-Consistency & Lifecycle Management

- **Startup is always a recovery event:** The system does not distinguish a clean boot from crash recovery. Reconciliation runs unconditionally: resolve config, assert schema compatibility, kill orphaned browsers, rebuild VRAM ledger from `/api/ps`, re-attest model digests, reconcile `budget_reservations`, validate quotas.
- **Node Idempotency Contract (FR13):** Every workflow node is re-executed from its last checkpoint after a crash. Ingestor is idempotent by content address. External API calls carry an idempotency key (`SHA256(run_id ‖ node ‖ attempt ‖ prompt_digest)`) written to `budget_reservations` *before* dispatch.
- **On resume:** `settled` → re-read response from `events` table (never re-issue); `reserved` + `provider_request_id` → orphan and alert; no row → dispatch.
- **Checkpoint Compatibility (ADR-010):** Forward-only migration, fail closed. Trust-layer fields are never defaulted. If a migration cannot faithfully reconstruct `lineage_attestation_status`, it must fail.
- **Graceful Shutdown (FR12):** `SIGTERM` → stop accepting runs → checkpoint active run at next node boundary → release VRAM/budget → `SIGKILL` browsers → flush traces. 120s timeout.
- **Maintenance Mode:** Operator flag. New runs refused; active run finishes; queued runs stay durable.

### 6.10 Configuration & Resource Governance

- **Configuration Classes:** **Static** (Lineage registry, sensitivity policy, ZDR posture, VRAM limits, budget ceiling, storage quotas, CPU partitions) is immutable after startup. **Dynamic** (Log levels, trace sampling, `ef_search`) is runtime, audited, and bumps `config_digest`.
- **Storage Quotas (FR11):** Physical isolation, not shared free space. Each store (`/data/artifacts`, `/data/postgres`, `/data/models`, `/data/traces`, `/data/metrics`) gets a dedicated filesystem/XFS quota. Watermarks: warn 80% · alert 90% · act 95%. At 95%, `artifacts` pauses the run (`paused_storage`); `traces` degrades to metrics-only.
- **Resource Limits:** `ulimit -n 4096` and `--pids-limit 512` per container.

### 6.11 Error Taxonomy & Degraded Modes

```text
ErosException (base)
├── ArtifactError          → quarantine, audit, continue run, log gap
├── ResourceError          → ContextCeilingExceeded | BudgetReservationFailed | VramSlotUnavailable | StorageQuotaExceeded
├── EvidenceError          → NoEvidenceFound | VerificationInconclusive
├── AttestationError       → fail closed; human review required
├── SensitivityError       → fail closed; never route external
├── CheckpointIncompatible → refuse to resume; evidence preserved
└── LlmOutputMalformed     → structural-correction retry (×2) → re-plan → human gate
```

**Degraded Modes:** Every degraded mode is declared, bounded, and disclosed in the **`degraded_mode_log`** table with a `NOT NULL exit_criterion`, `CHECK (> 0) max_duration`, and `capability_loss` rendered verbatim into every report footer.

---

## 7. Data Architecture

### 7.1 Stores & Owners

| Store | Technology | Owner | Transactional? |
|:---|:---|:---|:---|
| Relational + Vector | PostgreSQL 16 | Workflow, Ingestor, Verifier, Governor, Policy | **Yes — source of truth** |
| **Object Store** | NVMe `/data/artifacts` → hourly `restic` → NAS | Ingestor | **NO — write order is load-bearing** |
| Event Bus | Postgres `LISTEN/NOTIFY` + `events` | Row-writing components | Yes (same transaction) |

### 7.2 Core Schema

```sql
-- ═══════════════════════════════════════════════════════════════════════
-- Roles
-- ═══════════════════════════════════════════════════════════════════════
CREATE ROLE eros_analyst  NOLOGIN;
CREATE ROLE eros_verifier NOLOGIN;
CREATE ROLE eros_reporter NOLOGIN;
CREATE ROLE eros_ingestor NOLOGIN;
CREATE ROLE eros_governor NOLOGIN;
CREATE ROLE eros_ui       NOLOGIN;

GRANT USAGE ON SCHEMA public TO eros_analyst, eros_verifier, eros_reporter,
    eros_ingestor, eros_governor, eros_ui;

-- ═══════════════════════════════════════════════════════════════════════
-- Runs, Checkpoints, Queue
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'planning','searching','ingesting','analyzing','verifying','reporting','evaluating',
        'paused_budget','paused_approval','paused_storage','paused_maintenance',
        'published','failed','cancelled','insufficient_evidence'
    )),
    budget_envelope JSONB NOT NULL,
    budget_consumed JSONB NOT NULL DEFAULT '{}',
    computed_sensitivity TEXT DEFAULT 'open'
        CHECK (computed_sensitivity IN ('open','restricted','sensitive')),
    escalated BOOLEAN DEFAULT FALSE,
    lineage_attestation_status JSONB DEFAULT '{}',
    slot_transitions JSONB DEFAULT '[]',
    cancel_requested BOOLEAN DEFAULT FALSE,
    rocm_degradation_alert BOOLEAN DEFAULT FALSE,
    provenance JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE run_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question TEXT NOT NULL,
    envelope JSONB NOT NULL,
    sensitivity TEXT NOT NULL DEFAULT 'open',
    priority INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    checkpoint_data JSONB NOT NULL,
    node_name TEXT,
    runstate_version INT NOT NULL DEFAULT 4,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_checkpoints_run ON checkpoints(run_id);

-- ═══════════════════════════════════════════════════════════════════════
-- Artifacts & Chunks (Ingestor owner)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url TEXT,
    source TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE,
    trust_seed FLOAT NOT NULL DEFAULT 0.40
        CHECK (trust_seed >= 0.0 AND trust_seed <= 1.0),
    sensitivity TEXT NOT NULL DEFAULT 'open'
        CHECK (sensitivity IN ('open','restricted','sensitive')),
    snapshot_path TEXT NOT NULL,
    stale BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    locator TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR({{ EMBEDDING_DIM }}),
    fts tsvector,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chunks_artifact ON chunks(artifact_id);

-- ═══════════════════════════════════════════════════════════════════════
-- Draft Claim Evidence (Analyst staging — ADR-017 boundary)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE draft_claim_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    claim_text TEXT NOT NULL,
    primary_evidence_chunk_id UUID NOT NULL REFERENCES chunks(id),
    confidence NUMERIC(3,2) NOT NULL DEFAULT 0.00
        CHECK (confidence >= 0.00 AND confidence <= 1.00),
    analyst_model TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','promoted','rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at TIMESTAMPTZ
);

CREATE INDEX idx_dce_run ON draft_claim_evidence(run_id);
CREATE INDEX idx_dce_status ON draft_claim_evidence(status);

-- Promotion trigger: Verifier-mediated move from draft to claims
CREATE OR REPLACE FUNCTION promote_draft_claim()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO claims (run_id, text, status, primary_evidence_chunk_id, confidence, verification_kind)
    VALUES (NEW.run_id, NEW.claim_text, 'draft', NEW.primary_evidence_chunk_id, NEW.confidence, 'deterministic');

    NEW.status := 'promoted';
    NEW.promoted_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_promote_draft_claim
    BEFORE UPDATE OF status ON draft_claim_evidence
    FOR EACH ROW
    WHEN (NEW.status = 'promoted' AND OLD.status != 'promoted')
    EXECUTE FUNCTION promote_draft_claim();

-- ═══════════════════════════════════════════════════════════════════════
-- Claims & Evidence (Verifier-mediated)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','verified','contested','stale')),
    computed_sensitivity TEXT NOT NULL DEFAULT 'open'
        CHECK (computed_sensitivity IN ('open','restricted','sensitive')),
    verification_kind TEXT NOT NULL DEFAULT 'deterministic'
        CHECK (verification_kind IN ('deterministic','cross-family','same-family','external-cross-family')),
    primary_evidence_chunk_id UUID NOT NULL REFERENCES chunks(id),
    confidence NUMERIC(3,2) NOT NULL DEFAULT 0.00
        CHECK (confidence >= 0.00 AND confidence <= 1.00),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_claims_run ON claims(run_id);
CREATE INDEX idx_claims_evidence ON claims(primary_evidence_chunk_id);

CREATE TABLE claim_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN ('supports','contradicts')),
    checked_by_model TEXT NOT NULL,
    verification_kind TEXT NOT NULL
        CHECK (verification_kind IN ('deterministic','cross-family','same-family','external-cross-family')),
    confidence NUMERIC(3,2) NOT NULL DEFAULT 0.00
        CHECK (confidence >= 0.00 AND confidence <= 1.00),
    contest_strength NUMERIC(3,2)
        CHECK (contest_strength IS NULL OR (relation = 'contradicts' AND contest_strength >= 0.00 AND contest_strength <= 1.00)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_claim_evidence_claim ON claim_evidence(claim_id);
CREATE INDEX idx_claim_evidence_chunk ON claim_evidence(chunk_id);

-- ═══════════════════════════════════════════════════════════════════════
-- Gate 3: Report Provenance Ledger
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE report_sentences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ordinal INT NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('assertive', 'structural', 'disclosure', 'labeled_synthesis')),
    claim_id UUID REFERENCES claims(id) ON DELETE CASCADE,
    template_id TEXT,
    CHECK (
        (kind = 'assertive' AND claim_id IS NOT NULL AND template_id IS NULL) OR
        (kind IN ('structural', 'disclosure') AND template_id IS NOT NULL AND claim_id IS NULL) OR
        (kind = 'labeled_synthesis' AND claim_id IS NULL AND template_id IS NULL)
    ),
    UNIQUE (run_id, ordinal)
);

CREATE INDEX idx_report_sentences_run ON report_sentences(run_id);

-- ═══════════════════════════════════════════════════════════════════════
-- Budgets & Reservations (Governor owner)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period TEXT NOT NULL UNIQUE,
    external_ceiling  NUMERIC(10,2) NOT NULL DEFAULT 50.00,
    external_consumed NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE budget_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id UUID NOT NULL REFERENCES budgets(id),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    estimated_cost NUMERIC(10,2) NOT NULL,
    actual_cost    NUMERIC(10,2),
    provider TEXT NOT NULL,
    provider_request_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('reserved','settled','released','orphaned')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at TIMESTAMPTZ
);

CREATE INDEX idx_budget_res_run ON budget_reservations(run_id);
CREATE INDEX idx_budget_res_budget ON budget_reservations(budget_id);

-- ═══════════════════════════════════════════════════════════════════════
-- Events (Event Bus persistence)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    run_id UUID REFERENCES runs(id) ON DELETE CASCADE,
    payload JSONB NOT NULL DEFAULT '{}',
    latency_ms INT,
    token_count INT,
    model_name TEXT,
    cost_estimate NUMERIC(10,6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_run_type ON events(run_id, event_type);
CREATE INDEX idx_events_created ON events(created_at);

-- ═══════════════════════════════════════════════════════════════════════
-- Approvals (Human Gate durability)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    gate_name TEXT NOT NULL CHECK (gate_name IN ('budget','publish','sensitive_egress','memory_deletion')),
    decision TEXT CHECK (decision IN ('approved','rejected','expired')),
    actor TEXT NOT NULL DEFAULT 'system',
    expires_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ,
    correlation_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_approvals_run_gate ON approvals(run_id, gate_name);

-- ═══════════════════════════════════════════════════════════════════════
-- Degraded Mode Log
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE degraded_mode_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN (
        'sensitivity_fallback','same_family_verification','cpu_generation',
        'no_arbiter','oracle_unavailable','rocm_unavailable'
    )),
    exit_criterion TEXT NOT NULL,
    max_duration INTERVAL NOT NULL CHECK (max_duration > INTERVAL '0'),
    capability_loss TEXT NOT NULL,
    human_ack_by TEXT,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    CHECK (closed_at IS NULL OR closed_at > opened_at)
);

CREATE INDEX idx_dml_run ON degraded_mode_log(run_id);

-- ═══════════════════════════════════════════════════════════════════════
-- Groundedness Kernel Results (Gate 4 blocking evidence)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE groundedness_kernel_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL CHECK (verdict IN ('UNGROUNDED','INDETERMINATE')),
    missing_numbers TEXT[],
    missing_dates TEXT[],
    missing_entities TEXT[],
    missing_quotations TEXT[],
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entity_tolerance FLOAT NOT NULL DEFAULT 0.25,
    number_tolerance FLOAT NOT NULL DEFAULT 0.005
);

CREATE INDEX idx_gkr_claim ON groundedness_kernel_results(claim_id);

-- ═══════════════════════════════════════════════════════════════════════
-- Oracle Gold Set (Calibration corpus — M6, M7)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE oracle_gold_set (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_text TEXT NOT NULL,
    chunk_texts TEXT[] NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('grounded','ungrounded')),
    labeller TEXT NOT NULL,
    adjudicator TEXT NOT NULL,
    contains_number BOOLEAN DEFAULT FALSE,
    contains_date BOOLEAN DEFAULT FALSE,
    contains_entity BOOLEAN DEFAULT FALSE,
    contains_quotation BOOLEAN DEFAULT FALSE,
    adversarial_variant TEXT CHECK (adversarial_variant IN (
        'scale_unified','iso_date','fuzzy_entity','normalized_quote',
        'percent_vs_decimal','dmy_vs_mdy','ocr_noise','paraphrase'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (labeller <> adjudicator)
);

CREATE INDEX idx_ogs_label ON oracle_gold_set(label);
CREATE INDEX idx_ogs_adversarial ON oracle_gold_set(adversarial_variant);

-- ═══════════════════════════════════════════════════════════════════════
-- Audit (Policy owner — WORM)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- WORM enforcement triggers
CREATE OR REPLACE FUNCTION audit_worm_protect()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'WORM violation: % on audit table is prohibited', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_worm_update
    BEFORE UPDATE ON audit
    FOR EACH STATEMENT
    EXECUTE FUNCTION audit_worm_protect();

CREATE TRIGGER trg_audit_worm_delete
    BEFORE DELETE ON audit
    FOR EACH STATEMENT
    EXECUTE FUNCTION audit_worm_protect();

CREATE TRIGGER trg_audit_worm_truncate
    BEFORE TRUNCATE ON audit
    FOR EACH STATEMENT
    EXECUTE FUNCTION audit_worm_protect();

-- ═══════════════════════════════════════════════════════════════════════
-- Role Grants
-- ═══════════════════════════════════════════════════════════════════════
-- Analyst
GRANT INSERT, SELECT ON draft_claim_evidence TO eros_analyst;
GRANT SELECT ON chunks TO eros_analyst;
GRANT SELECT ON artifacts TO eros_analyst;
GRANT SELECT ON runs TO eros_analyst;

-- Verifier
GRANT INSERT, SELECT, UPDATE ON claims TO eros_verifier;
GRANT INSERT, SELECT, UPDATE ON claim_evidence TO eros_verifier;
GRANT UPDATE (status, promoted_at) ON draft_claim_evidence TO eros_verifier;
GRANT SELECT ON chunks TO eros_verifier;
GRANT SELECT ON artifacts TO eros_verifier;
GRANT INSERT, SELECT ON groundedness_kernel_results TO eros_verifier;

-- Reporter
GRANT INSERT, SELECT ON report_sentences TO eros_reporter;
GRANT SELECT ON claims TO eros_reporter;
GRANT SELECT ON chunks TO eros_reporter;

-- Ingestor
GRANT INSERT, SELECT ON artifacts TO eros_ingestor;
GRANT INSERT, SELECT ON chunks TO eros_ingestor;

-- Governor
GRANT INSERT, SELECT, UPDATE ON budgets TO eros_governor;
GRANT INSERT, SELECT, UPDATE ON budget_reservations TO eros_governor;

-- UI / Human Gate
GRANT INSERT, SELECT, UPDATE ON approvals TO eros_ui;
GRANT SELECT ON runs TO eros_ui;

-- Event Bus (all pipeline roles)
GRANT INSERT, SELECT ON events TO eros_analyst, eros_verifier, eros_reporter,
    eros_ingestor, eros_governor, eros_ui;

-- Audit (restricted)
GRANT INSERT ON audit TO eros_governor;
GRANT SELECT ON audit TO eros_governor, eros_ui;
```

### 7.3 Operational Telemetry (Non-Canonical Store)

The following are **not** in the core `runs` table but are captured via the LIL event bus into the `events` table or downstream observability stores. They are essential for operations but not for trust or resumption:

- `gate_classification`, `gate_confidence` — Heuristic Gate output
- `fallback_chain_used` — Router resolution path
- `context_ceiling_hits` — Context truncation avoidance counter
- `correlation_id` — LIL-scoped request tracing
- `escalation_reason` — In-session promotion trigger
- `ledger_sha256` — Report Ledger integrity (computed at publish, stored in `events`)

This separation keeps the core schema lean and trust-critical while preserving full observability.

### 7.4 Provenance at Every Hop

Chunk → claim → report-sentence links are **foreign keys, not prose**. The chain reaches all the way back to the binary, image, schema, and model digests that produced the run (`runs.provenance`).

### 7.5 Retention

- **Artifacts:** Kept (they *are* the evidence). Screenshots downsampled at 30 days.
- **Events:** 90 days (nightly cron).
- **Traces:** Payload-bearing 14 days → downsampled to metrics.
- **Checkpoints:** Latest 3 per run + terminal; rest dropped on terminal status.
- **Audit:** Append-only, WORM. Archival to Parquet at 1 year (migration, never deletion).

---

## 8. Security Architecture

### 8.1 Local-First Zero-Trust

Components authenticate with locally-issued scoped tokens. Secrets in age/OS-keyring vault. TLS optional on localhost, mandatory for LAN. Trust configuration is immutable after startup (§6.10).

### 8.2 Sensitivity Policy — The Crown Jewel

Labels flow with data from ingestion. The Router enforces them as a **hard constraint evaluated before any scoring**. Evaluated **per claim** (`max(sensitivity of supporting evidence chunks)`), reducing blast radius for mixed-sensitivity investigations.

### 8.3 Trust-Layer Guarantees

1. Sensitive content **never** leaves the machine.
2. Verification is **cross-family** unless degraded — and degradation is **labeled**.
3. A declared cross-family verification is backed by **attested resident weights**.
4. The sensitivity gate's **own miss rate is measured and gated** (≥0.98 recall).
5. **VRAM occupancy is deterministic and ledgered.**
6. External calls go **only to ZDR-capable providers**, are **fully audited**, and are **never double-billed** across a crash.
7. Audit history is **immutable**; archival is not a delete path.
8. **No claim exists without evidence** — enforced structurally by the database.
9. **A run that finds nothing says so.**
10. **No resource is reserved by an owner that cannot be found again.**
11. **A resumed run never double-spends and never double-writes.**
12. **A checkpoint the system cannot faithfully read is refused, not defaulted.**
13. **Every sentence in a published report** resolves to a verified claim, a whitelisted template, or a **disclosed** synthesis label. **Citation coverage is 1.0 by construction.**
14. **A claim whose numbers, dates, entities or quotations appear in no cited chunk cannot be published** — proven by a kernel in which **no model participates**.
15. **A verification cannot be labelled cross-family** unless the models are genuinely different families and both are attested.
16. **The Analyst cannot write verified evidence** — enforced by **privilege**, not convention.
17. **No release gate may be set below its measured achievable optimum.**

**Derived Operational Guarantees (non-numbered):**
- Every degraded mode is declared, bounded, disclosed, and has an exit criterion.
- Configuration that a run's guarantees depend on cannot change under it.
- The Oracle is calibrated against human-labelled gold, never against the Judge.
- No DDL is canonical until it has executed.

### 8.4 Abuse Paths & Incident Response

- **Prompt-injected web content:** Retrieved content is strictly untrusted **data**. No tool-calling exposed over retrieved text. No content-stripping/sanitization layer is claimed (recognized as anti-pattern). An honest gap beats a fake defense.
- **Sensitive-egress incident:** Prevention over remediation. ZDR is a hard requirement for admission. On discovery of a false negative, emit `SENSITIVE_EGRESS_INCIDENT`, halt new runs, and present deletion-request workflow (via whatever mechanism provider actually offers).

---

## 9. Performance, Infrastructure & Resource Governance

### 9.1 Resource Budget

- **VRAM 16 GB:** ≤ 13.5 GB steady. Worst case ~9.0 GB under Slot Ledger.
- **DRAM 96 GB:** ~60 GB working. Postgres buffers (16GB), embed/rerank (~2GB), CPU Classifier (~2.5GB), warm HNSW (~10GB).
- **NVMe 2 TB:** ~1.2 TB, quota-enforced. 800GB artifacts, 200GB Postgres, 150GB models, 50GB traces/metrics.
- **External $:** $50/month hard cap. Atomic reservation with owner.

### 9.2 CPU Optimization & Partitioning

- **GPU exclusively for generation.** Embeddings, reranking, and classification on CPU.
- **AVX-512 VNNI is the primary path** on Zen 5's native 512-bit datapath.
- **Explicit Partitioning (cpuset):**
  - *Normal mode:* Embeddings/reranker (Cores 0-6, CCD0), Maintenance lane (Core 7, CCD0), CPU Classifier (Cores 8-11, CCD1), Postgres (Cores 12-15, CCD1).
  - *Degraded mode (CPU fallback):* Embeddings (0-3), Classifier (4-5), Postgres (6-7), `llama.cpp` generation (8-15, CCD1). p95 NFR relaxed to 180 min.

### 9.3 Tiered KV-Cache Subsystem (Phase 2)

When context exhaustion is measured (ADR-002 flip), the LIL `kvcache.*` interface activates a feature-flagged vLLM ≥0.20.0 + LMCache MP sidecar. Tiers: L1 host DRAM, L2 `fs` adapter on NVMe. **NVMe tier has no GPU access; GPU↔NVMe always stages through DRAM.** FP8 on RDNA4 is a gate, not a default.

### 9.4 First Bottleneck Prediction

Verification-stage inference throughput, past ~50 load-bearing claims. Remedies: batch verification prompts → raise Checker context → *only then* consider vLLM (ADR-002).

### 9.5 Resource Quota Enforcement

Storage: physical isolation via XFS project quotas. Watermarks 80/90/95%. At 95%, `artifacts` pauses run (`paused_storage`); `traces` degrades to metrics-only. `ulimit -n 4096` and `--pids-limit 512` per container.

---
## 10. Failure Modes & Recovery

| Component | Failure | Blast radius | Detection | Recovery / Degradation |
|---|---|---|---|---|
| Ollama / GPU | ROCm crash, OOM | Inference stalls | Health probe | Auto-restart; CPU ladder with degraded partition. **Slot Ledger prevents load-time OOM.** |
| **VRAM slot contention** | Load requested while slot occupied | One adjudication/eval step | Ledger + `/api/ps` pre-check | Evict-then-load under mutex; queue if in flight. Never partial load. |
| VRAM ledger divergence | Router ≠ `/api/ps` | Potentially all runs | Reconciliation at transition | **Page immediately.** |
| External LLM API | Outage, 429 | Escalated tasks | Circuit breaker | Local fallback + report disclosure |
| **External call, crash mid-flight** | Reservation held, possibly billed | Budget ceiling + provenance | Startup reconciliation | Idempotency key: `settled` → re-read from `events`; `reserved` + req_id → `orphaned`. **Never blindly retry.** |
| **Crash between artifact write and row commit** | Bytes on disk, no row | None — by design | Nightly reconciliation | Orphaned file swept after 24h. |
| **Row committed before bytes durable** | **Dangling `snapshot_path`** | **Permanent corruption** | — | **Structurally impossible.** fsync → rename → *then* commit. |
| **Retriever finds nothing** | Zero chunks above threshold | One run | Evidence Sufficiency Gate | Bounded re-plan → `insufficient_evidence`. **Never synthesize from nothing.** |
| **Storage quota exhausted** | One store at 95% | One store — never a neighbour | Watermark | Per-store action. Physical isolation prevents cross-store starvation. |
| PostgreSQL | Crash | Everything | Probe | Restart. Continuous WAL archiving. Weekly base + continuous WAL → **RPO < 1 h**. **RTO < 4 h** (validated in Phase 1c). |
| **NVMe object store** | Disk failure | **All evidence** | Nightly reconciliation | `restic` hourly → NAS. **RPO < 1 h applies to complete system state.** Restore validated end-to-end as Phase 1c exit criterion. |
| **Checkpoint version mismatch** | Binary meets unreadable checkpoint | One run's state | ADR-010 assertion | **Migrate if registered; otherwise refuse.** Trust telemetry never defaulted. |
| **Gate 1/2/3/4 constraint violation** | DB trigger rejects transaction | One run | DB exception | **Transaction rejected. Actionable error returned.** Run pauses; operator alerted. |
| **Heuristic Gate drift** | Classifier going degenerate | Cost control / $50 ceiling | EWMA drift detector (ADR-019) | Alert after 9.5 periods. Operational recalibration at <0.85 accuracy. |
| Budget breach | Ceiling hit | Active run | Governor | `paused_budget`, resumable. **A pause, not a failure.** |
| **Attestation mismatch** | Resident weights ≠ registry digest | Verification integrity | Startup + periodic + resume | **Fail closed at startup.** Mid-run → alert + `LINEAGE_ATTESTATION_FAILED` |

---

## 11. Observability, SLOs & Evaluation

### 11.1 Metrics

- **Cardinality rule:** No Prometheus label may carry an unbounded identifier (`run_id`, `claim_id`). Per-entity values live in Postgres and traces.
- **Prometheus Aggregation Dimensions (bounded cardinality):**
  - `status` ∈ {published, failed, cancelled, insufficient_evidence}
  - `model_family` ∈ {llama3, qwen2, phi4, external}
  - `gate_class` ∈ {corpus_retrieval, single_model, full_investigation}
  - `degraded_mode` ∈ {none, cpu_fallback, same_family, rocm_unavailable}
  Per-entity values (`run_id`, `claim_id`) live in Postgres and traces only.
- **Run Details Query Pattern:**
  The UI "Run Details" view queries Postgres (not Prometheus) for per-run telemetry:
  ```sql
  SELECT e.event_type, e.latency_ms, e.token_count, e.model_name, e.cost_estimate
  FROM events e WHERE e.run_id = %s ORDER BY e.created_at;
  ```
  This provides deep causality without cardinality explosion.
- **Trust layer:** `gate_accuracy`, `gate_drift`, `cross_family_verification_rate` (≥0.80), `sensitivity_recall` (≥0.98), `lineage_attestation_failure_rate` (**target 0**), `dgk_false_positive_rate` (<0.02).
- **Slot Ledger:** `model_loads_per_run` (p95 ≤ 5), `vram_ledger_divergence` (**any nonzero pages**).
- **Drift Detection (ADR-019):** **EWMA (λ = 0.2, L = 2.962)** replaces the 3σ Shewhart rule. ARL₁ = 9.5 vs 42.0 for 1σ drift at the same false-alarm rate.
- **Oracle Divergence:** `window_n ≥ 22` (derived from measured σ=0.25) before any divergence flag may fire. Disagreements attributed against human gold (`judge_wrong` / `oracle_wrong` / `both_wrong` / `ambiguous`).

### 11.2 LLM Traces

Langfuse. Every node span: `prompt_ref`, `completion_ref`, model, slot, tokens, cost, latency. **Reference-only.** Emitted through shared wrapper; components never call SDK directly. Traces are lossy telemetry and excluded from the commit path.

### 11.3 Eval Gates

- **Citation coverage = 1.0** exactly (structural).
- **Groundedness ≥ 0.9** gates publishing (advisory layer above Gate 4).
- **Nightly:** Retrieval precision/recall, artifact integrity reconciliation, trace downsampling.
- **Weekly:** Full groundedness/coverage benchmark, **sensitivity recall ≥ 0.98**.
- **Quarterly:** Full restore drill (DB from WAL + artifacts from NAS). Any failure is Sev-1.

### 11.4 Operational SLOs

| SLO | Target |
|---|---|
| Startup reconciliation duration | p95 < 30 s |
| Checkpoint commit latency | p95 < 500 ms |
| Recovery time (crash → run resumed) | p95 < 5 min |
| Restore success rate | **100%** |
| **Orphaned budget reservations** | **0** |

---

## 12. Testing Strategy

| Category | Scope | Method |
|---|---|---|
| Contract tests | Every component API | Schema + auth + limits |
| **Graph shape assertion** | **C9 phase-batching** | Static analysis of compiled graph → **fail CI if Verifier reachable from inside Analyst loop** |
| **Node idempotency** | **FR13** | Re-execute every node twice from same checkpoint → identical state, zero double-writes |
| **Slot ledger** | Exclusivity · evict-then-load · divergence | Force Arbiter→Judge under load (**serialized, no OOM**) · load with no headroom (**evict-then-load**) · out-of-band `ollama stop` (**divergence fires**) |
| **Zero-evidence claim** | DB constraint | Attempt to commit a claim with `primary_evidence_chunk_id = NULL` → **transaction rejected** |
| **Negative Test Suite (Gates 1-4)** | **Gate Enforcement** | Attack every gate: commit zero-evidence claim (Gate 1), publish draft-only evidence (Gate 2), point sentence at draft claim (Gate 3), Judge scores 0.91 but DGK proved ungrounded (Gate 4). **15/15 must hold.** |
| **Attestation of the attester** | The hash function itself | Known-bad and known-good files → reject and accept. |
| **Artifact write ordering** | **§6.5** | Crash (SIGKILL) between `rename` and `COMMIT` → orphaned file, no dangling pointer. Verify reverse is impossible. |
| **External-call idempotency** | **§6.4** | Sever network mid-call, crash, resume → response re-read from `events`, call NOT re-issued, budget NOT double-charged. |
| **Checkpoint compatibility** | **ADR-010** | Golden checkpoints from every prior `runstate_version` → migrate or refuse; **never default a trust field**. |
| Soak | Stability | 20 sequential investigations, no VRAM/DRAM/FD creep · 5 CPU-fallback · 5 API-outage · 5 max-context · 5 mixed-sensitivity |

**Canonical Negative Test Error Strings:**

| Attack | Expected Error String | Gate |
|---|---|---|
| Commit claim with zero evidence | `GATE 1: claim %s has no supporting evidence chunk (NoEvidenceFound).` | Gate 1 |
| Publish run with draft-only evidence | `GATE 2: run %s has claim(s) with no verified evidence.` | Gate 2 |
| Assertive sentence at draft claim | `GATE 3: run %s has assertive sentence(s) whose claim is missing/draft/stale.` | Gate 3 |
| Judge scores 0.91, DGK proved ungrounded | `GATE 4: run %s has claim(s) proved ungrounded. No model vote overrides.` | Gate 4 |
| Same-family labelled cross-family | `ATTESTATION: check labelled cross-family but drafter and checker are BOTH family %s.` | ADR-017 |
| Infeasible target stored | `violates check constraint "target_must_be_achievable"` | ADR-018 |

**CI Assertion:**
```python
# Pseudo-code for negative test suite
assert error_message.startswith("GATE 1:") and "NoEvidenceFound" in error_message
```

### 12.1 Failure-Injection Suite (Phase 1c gate)

**Concrete Pass/Fail Criteria per Scenario:**

| Scenario | Pass Criteria |
|---|---|
| `SIGKILL` workflow mid-node | Checkpoint exists with `node_name` and `runstate_version = 4`; resume succeeds within 5 min; zero orphaned budget rows. |
| Fill store to 100% | Run pauses (`paused_storage`); no cross-store starvation; `df` shows isolated quota enforcement. |
| Exhaust VRAM out-of-band | `vram_ledger_divergence` alarm fires within 5s; OOM killer does not terminate Postgres. |
| Sever network mid-external-call | Budget reservation status = `orphaned`; `events` table has no duplicate `provider_request_id`; ceiling not breached. |
| NAS unreachable during `restic` | `restic` failure warns; run continues; RPO degrades to nightly local copy with disclosed footer. |
| Corrupt checkpoint row | `CheckpointIncompatible` raised; run refuses to resume; evidence preserved in `artifacts` + `chunks`. |
| Restart Postgres mid-transaction | WAL replay succeeds; no partial commits in `claims` or `budget_reservations`; `audit` table has `RESTART_RECOVERED` entry. |
| Restart Ollama mid-generation | Generation slot re-attested; `/api/ps` reconciled; no `LINEAGE_ATTESTATION_FAILED` false positive. |
| Exhaust file descriptors | `EMFILE` handled gracefully; `ulimit -n 4096` enforced per container; run pauses with `ResourceError`. |

---

## 13. Implementation Roadmap

### 13.0 Phase 0 — Infrastructure Bootstrap *(gated; complete before any code runs)*

- Docker Compose base (Postgres 16 + pgvector, no Redis).
- ROCm + Ollama installed, **version pinned**. `OLLAMA_MAX_LOADED_MODELS=2`, `FLASH_ATTENTION=1`, `KV_CACHE_TYPE=q8_0`.
- Storage quotas (five dedicated filesystems). `restic` replication to NAS.
- Langfuse ref-only tracing wrapper.
- Alembic `EMBEDDING_DIM` interpolation. Attestation hash cross-validation.
- **LIL Skeleton:** Sync API + event bus + evidence log (append-only, hash-chained) + budget metering.

**Day-by-Day Schedule:**

> **Day 0:** Apply schema v3.2 with `ON_ERROR_STOP=1`. Run negative suite (15/15 gates hold). Stand up `make verify-spec` in CI.  
> **Day 1:** LangGraph checkpointer SIGKILL sweep. Reconcile `budget_reservations`.  
> **Day 2 (M1–M4):** Measure `W_d, K_d, W_c, K_c, R` on actual card.  
> **Day 3 (ADR-011.1):** Measure Arbiter ladder (granite-3.1-2b → 3b → 8b). Smallest clearing `acc ≥ 0.80` AND `q8_0` stability wins.  
> **Day 4:** Ollama pin + env contract re-validation.  
> **Day 5:** Storage isolation; WAL archive config.  
> **Day 6 (ADR-015):** Wire DGK into Verifier. Kernel self-test 18/18.  
> **Day 7 (M5):** Tier-2 NLI latency on VNNI path. ≤30ms/pair or demote to nightly.  
> **Day 8 (M6):** Gold Set v1 (**1,000 claims**, dual-labelled). Measure `σ(judge − oracle)`.  
> **Day 9 (M7):** DGK false-UNGROUNDED rate against Gold Set. <2% → arm Gate 4 blocking. ≥2% → shadow mode. Measure `d̂, π̂`. Write active `gate_operating_point`. `derived_target ≥ achievable_cost`.

### 13.1 Phase 1 — Core Loop & Trust Layer *(Weeks 1–10)*

- **1a (Weeks 1-4):** ROCm validation. Measure M1-M4 (KV cache/load latency). Select Arbiter family. Postgres schema + LangGraph skeleton. Ingestor with fsync→rename→commit ordering. Retriever + Evidence Sufficiency Gate. Reporter writing the Report Ledger (Gate 3).
- **1b (Weeks 5-8):** Sequential Slot Ledger. Budget reservation ledger. External escalation with ZDR. **Postgres roles** (`eros_analyst`, `eros_verifier`, `eros_reporter`). **Wire DGK into Verifier** (Gate 4). **Gate 1 & Gate 2 triggers armed.** EWMA drift detector. Gold Set v1 (**1,000 claims**, dual-labelled).
- **1c (Weeks 9-10):** Error taxonomy. 20-run soak. **Failure-injection suite**. M7 (DGK false-positive rate < 2% arms Gate 4 in blocking mode).

### 13.2 Phase 2 — Platform & Memory

- **Hierarchical memory + fleet:** H-MEM 4-layer index; GAM buffer/consolidation as bounded background loop; EM-LLM-approximate segmentation. Orchestrator-worker delegation. IUI lifecycle (pin/distill/offload).
- **Tiered KV subsystem (flag ON for dogfood only):** Pin vLLM ≥0.20.0, LMCache MP sidecar. Degrade-to-Ollama tested explicitly. FP8 only after `check-fp8-kernels` passes.

### 13.3 Phase 3 — Engine & Self-Improvement

- **Self-improvement, one lane at a time:** Harness manifest + commit worker + protected lane + promotion gate with held-out family set.
- Enable `harness_ace` (overlay/canary/graduation path) first. Then `harness_ahe` in offline scheduled windows. SGH-style plan graphs promoted from hand-authored templates only after epoch migration + quarantine is audit-tested.

### 13.4 Phase 4 — OS

- Unlock multi-optimizer concurrency only when H1/H3-style checks pass locally.
- Multi-agent fleet deployed with battle-tested conflict-resolution protocol. `memory_sync_shimi` for second machine. Multi-node scale-out design pass.

---
## 14. Vision Backlog (Phase 2+)

| Item | Proposed ADR | Flip condition | Deferred rationale |
|:---|:---|:---|:---|
| Qdrant vector DB | ADR-003-revisit | > 5M vectors OR p99 > 150 ms | Single moving part sufficient |
| Neo4j knowledge graph | New | Entity-centric queries recur OR adjudication needs relationship context | Atomic FKs sufficient for v1 provenance |
| Temporal workflow engine | ADR-001-revisit | Investigations span > 24 h OR multi-node | Postgres checkpointing sufficient |
| **vLLM + LMCache tier** | ADR-002-revisit | Measured context exhaustion; **or PagedAttention-class runtime makes Slot Ledger obsolete** | Ollama sufficient; **vLLM's paged KV cache would supersede §6.6.4 entirely** |
| **Time-budget governor** | New | Wall-clock overruns recur | VRAM and money are governed; time is only *timed* |
| Multi-agent fleet (20+) | New | Stages demonstrably contend for specialization AND conflict protocol battle-tested | A fleet multiplies failure modes |
| **Cognitive Memory (H-MEM/GAM)** | New | pgvector p99 > 100 ms at scale OR measured long-context recall degradation | Positional-encoding complexity premature |
| TEE / SEV-SNP | New | Regulated data AND hardware available | Incompatible with target platform |
| LangSmith observability | — | *Never* | SaaS dependency violates local-first |

---

## 15. Appendix A — Architecture Decision Records

| ADR | Decision | Status |
|:---|:---|:---|
| **ADR-001** | Orchestration: **LangGraph + Postgres checkpointer**; Temporal deferred. | Accepted |
| **ADR-002** | Long-context tier: **defer vLLM + LMCache** behind reserved flag. | Accepted |
| **ADR-003** | v1 datastore: **PostgreSQL monostore** (+pgvector +FTS); Qdrant/Neo4j deferred. | Accepted |
| **ADR-004** | Sensitivity enforcement: **hard constraint in Model Router, evaluated first.** | Accepted |
| **ADR-005** | Verifier lineage independence: **cross-family pinning**, two-layer enforcement. | Accepted |
| **ADR-009** | **Operational consolidation.** **Sequential Slot Ledger** (replaces resident-pair VRAM model) · artifact replication + system-wide RPO · reference-only tracing · ZDR as hard admission requirement · evidence sufficiency gate + zero-evidence DB constraint. | Incorporated |
| **ADR-010** | **Checkpoint & schema compatibility.** Forward-only migration, fail closed. Trust telemetry never defaulted. | Accepted |
| **ADR-011.1** | **Arbiter Constraint Restoration.** `≤4B` cap justified by measurement, not false unsatisfiability. Ladder evaluation selects smallest model clearing `acc ≥ 0.80`. | Accepted |
| **ADR-014** | **Runtime lineage attestation (GGUF blob digests).** Superseded by ADR-017; attestation now enforced via DB privilege boundary + manifest. | Superseded |
| **ADR-015** | **Deterministic Groundedness Kernel & Oracle Gold Set.** Tier-1 DGK (blocking, no model) + Tier-2 NLI (advisory). Gold Set dual-labelled. | Accepted |
| **ADR-016** | **Gate 3: Report Provenance Ledger.** Citation coverage = 1.0 by construction. ≥95% eval gate deleted. | Accepted |
| **ADR-017** | **Role-Enforced Maker–Checker & Attested Verification Constraint.** DB privilege boundary. Analyst cannot write verified evidence. | Accepted |
| **ADR-018** | **Feasible Gate Economics.** Derive target from measured ROC. Bind escalation ceiling to $50 budget. DB refuses infeasible targets. | Accepted |
| **ADR-019** | **Powered Drift & Divergence Detection.** EWMA replaces 3σ rule. `window_n ≥ 22` for Oracle divergence. | Accepted |
| **ADR-020** | **Gate 1 Structural Migration.** Structural `NOT NULL` FK replaces deferred trigger. `draft_claim_evidence` staging table required. Flip: promotion latency p95 < 50ms; rollback if > 200ms or race in 5% of transactions. | Accepted |
| **ADR-021** | **Universal Interface Layer (LIL).** Typed sync/async boundary enforcing evidence capture, budget metering, and isolation. | Accepted |
| **ADR-022** | **Model Versioning Manifest.** JSON manifest with schema validation and attestation checksum cross-validation. | Accepted |

---

## 16. Appendix B — Platform Ground Truth

| # | Fact | Consequence for EROS |
|---|------|----------------------|
| 1 | llama.cpp/Ollama **pre-allocate the entire KV cache at model load** for the full `num_ctx`. | VRAM occupancy is a **fixed reservation**. The resident pair sat at ~14.5 GB permanently. |
| 2 | `num_ctx` **cannot be overridden per-request**; models inherit GGUF-declared context. | **Derived Modelfiles are mandatory.** Base tags blocked. |
| 3 | **No API clears KV cache while keeping weights resident.** | The only lever is unload. **Sequential Slot Ledger.** |
| 4 | A model pinned with `keep_alive: -1` is **not evicted**; Ollama fails incoming load. **Never pin.** **`keep_alive: -1` does not survive restart. Ledger rebuilt from `/api/ps` at startup.** | Router owns residency. Unconditional startup reconciliation (§6.9) justified. |
| 5 | `OLLAMA_KV_CACHE_TYPE=q8_0` roughly **halves KV memory**; requires `FLASH_ATTENTION=1`. | Both enabled. Gemma removed due to regression under KV quantization. |
| 6 | `GET /api/ps` reports `size_vram` per loaded model. | **The ledger's reconciliation oracle.** |
| 7 | Ollama's environment-variable names have **churned across releases**. | **Pin version. Re-validate env contract on every bump.** |
| 8 | NVMe tier has **no direct GPU access**; GPU↔NVMe stages through DRAM. | Latency budgets and KV-tiering diagrams reflect this topology. |

---

## 17. Appendix C — Trust Layer Implementation Checklist

**Gateway + LIL**
- [ ] LIL Sync API + Event Bus active; every call creates an evidence record
- [ ] `run_queue` persisted; `FOR UPDATE SKIP LOCKED`; rebuilt at startup
- [ ] `DELETE /research/{id}` → cooperative cancellation at node boundaries
- [ ] MCP Server Contract enforced at tool boundary (if enabled)
- [ ] Degraded Mode UI Interaction (FR18) implemented: explicit fast-abort choice

**Gate**
- [ ] Derived target computed from measured ROC; DB refuses infeasible target
- [ ] In-session escalation: three triggers, **at most one**, one tier, disclosed
- [ ] Escalation triggers quantified (e.g., avg `confidence` < 0.70)

**Workflow**
- [ ] RunState v4; checkpoint captures every field
- [ ] **Graph-shape CI assertion: phase-batched, no Analyst/Verifier interleaving**
- [ ] **Every node idempotent or compensating**
- [ ] Checkpoint = one transaction; **no partial checkpoint possible**
- [ ] Resume → re-attest every participating slot first
- [ ] Human Gate: 24h timeout, releases VRAM/budget while open

**Router + Slot Ledger**
- [ ] `MAX_LOADED_MODELS=2`, flash attention, `q8_0`, `NUM_PARALLEL=1`; **version pinned**
- [ ] **Postgres advisory lock (`pg_advisory_xact_lock`) acquired before Ollama API calls**
- [ ] Evict-then-load under one GPU mutex; poll `/api/ps` until slot clears
- [ ] Ledger rebuilt from `/api/ps` at startup — **never from checkpoint**
- [ ] `vram_ledger_divergence` pages on any nonzero value
- [ ] Context Ceiling: derived Modelfiles only; base tags blocked
- [ ] Model Versioning: manifest contains semantic version + SHA-256 digest

**Trust Chain (Gates 1-4)**
- [ ] Gate 1: `claims.primary_evidence_chunk_id` `NOT NULL` enforced; `draft_claim_evidence` staging table active; promotion path tested
- [ ] Gate 2: `g10_runs_publish_requires_verified_evidence` trigger attached and tested
- [ ] Gate 3: `report_sentences` ledger active; citation coverage = 1.0 exactly
- [ ] Gate 4: DGK wired; Tier-2 NLI advisory; M7 false-positive < 2% before arming
- [ ] DGK matching logic defined (Numbers, Dates, Entities, Quotations)
- [ ] ADR-017: `eros_analyst` denied INSERT on `claim_evidence`; Attested XFAM constraint active
- [ ] Trigger execution order: g00 → g10 → g20 → g30 (alphabetical, cheapest first)

**Crash consistency**
- [ ] **Ingestor: fsync → fsync dir → rename → THEN commit**
- [ ] **Budget reservations carry run_id, node, idempotency key**
- [ ] **External calls carry idempotency key written BEFORE dispatch**
- [ ] **Startup runs full reconciliation, unconditionally**
- [ ] Graceful shutdown: 120s timeout, checkpoint at boundary, release resources

**Search & Collection**
- [ ] Browser pool: 3 workers, Firejail/seccomp-bpf, 2GB RSS cap, timeout ladder
- [ ] Embedding/Reranker: `bge-large-en-v1.5`, AVX-512 VNNI, int8, 256-chunk batches
- [ ] Chunking: 512 tokens, 20% overlap
- [ ] CPU Classifier: Phi-4-mini, cores 8-11

---

## 18. Appendix D — Open Assumptions Register

| ID | Assumption | Estimate | Measure | If worse |
|---|---|---|---|---|
| **D1** | Drafter KV @12k, `q8_0` | ~1.0 GB | Week 1a | Lever 1: `num_ctx` 12k → 8k |
| **D2** | Checker KV @12k, `q8_0` | ~0.75 GB | Week 1a | Lever 1 |
| **D3** | Model-load latency NVMe→DRAM→VRAM | <1 s (≤2B) · 1–2 s (8B) | Week 1a | Lever 2: pin Generation Slot; ≤1B on-demand |
| **D4** | ROCm throughput on RDNA 4 vs. NFR envelope | **Unknown.** "~30% below CUDA" is folklore. | **Week 1a, Day 1** | A2 contingency: Downsize Drafter to **Qwen-2.5-1.5B-Instruct** at 8k context, prefer external, relax to CPU-fallback NFR (requires user fast-abort consent) |
| **M5** | Tier-2 NLI latency (≤30ms/pair) | <30ms | Phase 0 Day 7 | Demote to nightly batch |
| **M6** | Per-claim `σ(judge − oracle)` | ≈ 0.25 | Phase 0 Day 8 | Re-derive `n` from measured σ |
| **M7** | DGK false-UNGROUNDED rate | < 2% | Phase 0 Day 9 | Loosen `entity_tolerance`; Gate 4 runs in shadow mode. Evaluated against **1,000-claim** Gold Set. |

*Combined degradation path (D1+D2+D3 land badly):* `num_ctx` → 8k **and** on-demand → 1B-class. Worst case ~8.1 GB — still inside ceiling. The design has somewhere to go.

---

*End of EROS v3.2 Canonical Architecture*
