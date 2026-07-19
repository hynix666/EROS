-- ═══════════════════════════════════════════════════════════════════════════
-- EROS v3.2 — Core Schema (canonical §7.2) + Trust-Chain Trigger DDL (§6.8)
--
-- Provenance:
--   * Everything under "CANONICAL §7.2" is verbatim from the frozen document
--     (EMBEDDING_DIM placeholder interpolated by scripts/apply_schema.py).
--   * Everything under "TRUST-CHAIN DDL MATERIALIZATION" implements triggers
--     that §6.8 names, §12 gives canonical error strings for, and Appendix C
--     requires attached — but for which the canonical document contains no
--     DDL. Per §0.1 (Executable Specification Mandate) they are materialized
--     here and logged in db/AMENDMENTS.md as proposed ADR-023.
--
-- Apply with:  psql ... -v ON_ERROR_STOP=1 -f schema.sql   (after interpolation)
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ═══════════════════════════════════════════════════════════════════════
-- CANONICAL §7.2 — Roles
-- ═══════════════════════════════════════════════════════════════════════
DO $$
BEGIN
    -- Roles are cluster-level; guard for idempotent re-apply on shared clusters.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eros_analyst')  THEN CREATE ROLE eros_analyst  NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eros_verifier') THEN CREATE ROLE eros_verifier NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eros_reporter') THEN CREATE ROLE eros_reporter NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eros_ingestor') THEN CREATE ROLE eros_ingestor NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eros_governor') THEN CREATE ROLE eros_governor NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eros_ui')       THEN CREATE ROLE eros_ui       NOLOGIN; END IF;
END $$;

GRANT USAGE ON SCHEMA public TO eros_analyst, eros_verifier, eros_reporter,
    eros_ingestor, eros_governor, eros_ui;

-- ═══════════════════════════════════════════════════════════════════════
-- CANONICAL §7.2 — Runs, Checkpoints, Queue
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
-- CANONICAL §7.2 — Artifacts & Chunks (Ingestor owner)
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
-- CANONICAL §7.2 — Draft Claim Evidence (Analyst staging — ADR-017 boundary)
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

-- Promotion trigger: Verifier-mediated move from draft to claims  (canonical)
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
-- CANONICAL §7.2 — Claims & Evidence (Verifier-mediated)
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
-- CANONICAL §7.2 — Gate 3: Report Provenance Ledger
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
-- CANONICAL §7.2 — Budgets & Reservations (Governor owner)
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
-- CANONICAL §7.2 — Events (Event Bus persistence)
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
-- CANONICAL §7.2 — Approvals (Human Gate durability)
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
-- CANONICAL §7.2 — Degraded Mode Log
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
-- CANONICAL §7.2 — Groundedness Kernel Results (Gate 4 blocking evidence)
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
-- CANONICAL §7.2 — Oracle Gold Set (Calibration corpus — M6, M7)
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
-- CANONICAL §7.2 — Audit (Policy owner — WORM)
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

-- ═══════════════════════════════════════════════════════════════════════════
-- ═══════════════════════════════════════════════════════════════════════════
--  TRUST-CHAIN DDL MATERIALIZATION  (proposed ADR-023 — see db/AMENDMENTS.md)
--
--  Implements the triggers §6.8 names, using the canonical error strings of
--  §12 ("Canonical Negative Test Error Strings"), in the trigger-name order
--  Appendix C requires: g00 → g10 → g20 → g30 (alphabetical on runs).
-- ═══════════════════════════════════════════════════════════════════════════
-- ═══════════════════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────────────
-- Run status transition table (§0.1 anticipates a "transition table").
-- Data-driven so the legal state machine is auditable and testable.
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE run_status_transitions (
    from_status TEXT NOT NULL,
    to_status   TEXT NOT NULL,
    PRIMARY KEY (from_status, to_status)
);

-- Core forward flow
INSERT INTO run_status_transitions (from_status, to_status) VALUES
    ('planning',  'searching'),
    ('searching', 'ingesting'),
    ('ingesting', 'analyzing'),
    ('analyzing', 'verifying'),
    ('verifying', 'reporting'),
    ('reporting', 'evaluating'),
    ('evaluating','published');

-- Bounded loops (re-plan / QA revision) and honest termination
INSERT INTO run_status_transitions (from_status, to_status) VALUES
    ('ingesting', 'searching'),              -- search fan-out iteration
    ('analyzing', 'searching'),              -- Evidence Sufficiency Gate re-plan
    ('evaluating','reporting'),              -- one bounded QA revision
    ('searching', 'insufficient_evidence'),
    ('ingesting', 'insufficient_evidence'),
    ('analyzing', 'insufficient_evidence');

-- Any active phase may fail, be cancelled, or pause (budget/approval/storage/maintenance)
INSERT INTO run_status_transitions (from_status, to_status)
SELECT a.s, t.s
FROM unnest(ARRAY['planning','searching','ingesting','analyzing','verifying','reporting','evaluating']) AS a(s)
CROSS JOIN unnest(ARRAY['failed','cancelled','paused_budget','paused_approval','paused_storage','paused_maintenance']) AS t(s);

-- Any pause may resume into any active phase (checkpoint knows where), or terminate
INSERT INTO run_status_transitions (from_status, to_status)
SELECT p.s, t.s
FROM unnest(ARRAY['paused_budget','paused_approval','paused_storage','paused_maintenance']) AS p(s)
CROSS JOIN unnest(ARRAY['planning','searching','ingesting','analyzing','verifying','reporting','evaluating','failed','cancelled']) AS t(s);

-- 'published', 'failed', 'cancelled', 'insufficient_evidence' are terminal: no outgoing rows.

-- ───────────────────────────────────────────────────────────────────────
-- g00 — legal status transition (fires first, alphabetically)
-- ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION g00_runs_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT EXISTS (
            SELECT 1 FROM run_status_transitions t
            WHERE t.from_status = OLD.status AND t.to_status = NEW.status
        ) THEN
            RAISE EXCEPTION 'GATE 0: illegal run status transition % -> % for run %',
                OLD.status, NEW.status, OLD.id;
        END IF;
    END IF;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER g00_runs_status_transition
    BEFORE UPDATE OF status ON runs
    FOR EACH ROW
    EXECUTE FUNCTION g00_runs_status_transition();

-- ───────────────────────────────────────────────────────────────────────
-- g01 — Gate 1 canonical message (ADR-020 keeps the structural NOT NULL;
-- a BEFORE ROW trigger fires *before* constraint checks, so attacks get
-- the canonical actionable error instead of a bare not-null violation).
-- ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION g01_claims_evidence_required()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.primary_evidence_chunk_id IS NULL THEN
        RAISE EXCEPTION 'GATE 1: claim % has no supporting evidence chunk (NoEvidenceFound).',
            COALESCE(NEW.id::text, '(new)');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER g01_claims_evidence_required
    BEFORE INSERT OR UPDATE OF primary_evidence_chunk_id ON claims
    FOR EACH ROW
    EXECUTE FUNCTION g01_claims_evidence_required();

-- ───────────────────────────────────────────────────────────────────────
-- g05 — Attested cross-family constraint on claim_evidence (ADR-017).
-- Reads runs.lineage_attestation_status (canonical JSONB), expected shape:
--   {"roles": {"drafter": {"model": "...", "family": "llama3", "attested": true},
--              "checker": {"model": "...", "family": "qwen2",  "attested": true},
--              ...}}
-- The Router records every participating model here before verification
-- writes; external ZDR-admitted checkers are recorded the same way.
-- ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION g05_claim_evidence_attested_xfam()
RETURNS TRIGGER AS $$
DECLARE
    las JSONB;
    drafter_family TEXT;
    checker_rec JSONB;
    checker_family TEXT;
    r RECORD;
BEGIN
    IF NEW.verification_kind NOT IN ('cross-family', 'external-cross-family') THEN
        RETURN NEW;  -- deterministic / same-family checks are not asserted as cross-family
    END IF;

    SELECT r2.lineage_attestation_status INTO las
    FROM claims c JOIN runs r2 ON r2.id = c.run_id
    WHERE c.id = NEW.claim_id;

    drafter_family := las #>> '{roles,drafter,family}';

    -- locate the checker record by model tag
    FOR r IN SELECT value FROM jsonb_each(COALESCE(las -> 'roles', '{}'::jsonb)) LOOP
        IF r.value ->> 'model' = NEW.checked_by_model THEN
            checker_rec := r.value;
            EXIT;
        END IF;
    END LOOP;

    IF drafter_family IS NULL OR checker_rec IS NULL THEN
        RAISE EXCEPTION 'ATTESTATION: model % has no lineage record for this run; cross-family label refused.',
            NEW.checked_by_model;
    END IF;

    IF COALESCE((checker_rec ->> 'attested')::boolean, false) IS NOT TRUE THEN
        RAISE EXCEPTION 'ATTESTATION: model % carries no live attestation; cross-family label refused.',
            NEW.checked_by_model;
    END IF;

    checker_family := checker_rec ->> 'family';
    IF checker_family = drafter_family THEN
        RAISE EXCEPTION 'ATTESTATION: check labelled cross-family but drafter and checker are BOTH family %.',
            drafter_family;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER g05_claim_evidence_attested_xfam
    BEFORE INSERT OR UPDATE ON claim_evidence
    FOR EACH ROW
    EXECUTE FUNCTION g05_claim_evidence_attested_xfam();

-- ───────────────────────────────────────────────────────────────────────
-- g10 — Gate 2: publish requires verified evidence
-- ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION g10_runs_publish_requires_verified_evidence()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'published' AND OLD.status IS DISTINCT FROM 'published' THEN
        IF EXISTS (
            SELECT 1 FROM claims c
            WHERE c.run_id = NEW.id
              AND (
                    c.status = 'draft'
                 OR NOT EXISTS (
                        SELECT 1 FROM claim_evidence ce
                        WHERE ce.claim_id = c.id AND ce.relation = 'supports'
                    )
              )
        ) THEN
            RAISE EXCEPTION 'GATE 2: run % has claim(s) with no verified evidence.', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER g10_runs_publish_requires_verified_evidence
    BEFORE UPDATE OF status ON runs
    FOR EACH ROW
    EXECUTE FUNCTION g10_runs_publish_requires_verified_evidence();

-- ───────────────────────────────────────────────────────────────────────
-- g20 — Gate 4: publish requires grounded claims (DGK verdicts).
-- Blocking by default; shadow mode via GUC `eros.gate4_mode` = 'shadow'
-- (configuration, not schema — set by the app from static config until
-- M7 < 2% arms blocking mode, per §6.8.1).
-- The *latest* kernel verdict per claim governs (claims may be re-checked
-- after a bounded revision).
-- ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION g20_runs_publish_requires_grounded_claims()
RETURNS TRIGGER AS $$
DECLARE
    mode TEXT := COALESCE(NULLIF(current_setting('eros.gate4_mode', true), ''), 'blocking');
BEGIN
    IF NEW.status = 'published' AND OLD.status IS DISTINCT FROM 'published' AND mode = 'blocking' THEN
        IF EXISTS (
            SELECT 1
            FROM claims c
            JOIN LATERAL (
                SELECT g.verdict
                FROM groundedness_kernel_results g
                WHERE g.claim_id = c.id
                ORDER BY g.checked_at DESC
                LIMIT 1
            ) latest ON TRUE
            WHERE c.run_id = NEW.id
              AND latest.verdict = 'UNGROUNDED'
        ) THEN
            RAISE EXCEPTION 'GATE 4: run % has claim(s) proved ungrounded. No model vote overrides.', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER g20_runs_publish_requires_grounded_claims
    BEFORE UPDATE OF status ON runs
    FOR EACH ROW
    EXECUTE FUNCTION g20_runs_publish_requires_grounded_claims();

-- ───────────────────────────────────────────────────────────────────────
-- g30 — Gate 3: publish requires report provenance.
-- Every assertive sentence must name a claim that is verified or contested
-- (never missing/draft/stale). A publish with zero sentences is refused —
-- coverage of an empty report is vacuous, not 1.0. [judgment, AMENDMENTS.md]
-- ───────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION g30_runs_publish_requires_report_provenance()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'published' AND OLD.status IS DISTINCT FROM 'published' THEN
        IF NOT EXISTS (SELECT 1 FROM report_sentences s WHERE s.run_id = NEW.id) THEN
            RAISE EXCEPTION 'GATE 3: run % has no report sentences; publish refused.', NEW.id;
        END IF;
        IF EXISTS (
            SELECT 1
            FROM report_sentences s
            LEFT JOIN claims c ON c.id = s.claim_id
            WHERE s.run_id = NEW.id
              AND s.kind = 'assertive'
              AND (c.id IS NULL OR c.status IN ('draft', 'stale'))
        ) THEN
            RAISE EXCEPTION 'GATE 3: run % has assertive sentence(s) whose claim is missing/draft/stale.', NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER g30_runs_publish_requires_report_provenance
    BEFORE UPDATE OF status ON runs
    FOR EACH ROW
    EXECUTE FUNCTION g30_runs_publish_requires_report_provenance();

-- ───────────────────────────────────────────────────────────────────────
-- Gate operating point (ADR-018): the DB refuses infeasible targets.
-- Named CHECK constraint yields the canonical §12 error string:
--   violates check constraint "target_must_be_achievable"
-- Written by Phase 0 Day 9 measurement; exactly one row active.
-- ───────────────────────────────────────────────────────────────────────
CREATE TABLE gate_operating_point (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    d_hat NUMERIC(8,6) NOT NULL,             -- measured detection prevalence
    pi_hat NUMERIC(8,6) NOT NULL,            -- measured class prior
    achievable_cost NUMERIC(12,6) NOT NULL,  -- min_t C(t; d_hat, pi_hat) from measured ROC
    derived_target NUMERIC(12,6) NOT NULL,   -- 1.15 × achievable_cost
    rho_budget NUMERIC(8,6) NOT NULL,        -- escalation ceiling from $50/mo
    active BOOLEAN NOT NULL DEFAULT FALSE,
    measured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT target_must_be_achievable CHECK (derived_target >= achievable_cost)
);

CREATE UNIQUE INDEX uniq_gate_operating_point_active
    ON gate_operating_point (active) WHERE active;

-- ═══════════════════════════════════════════════════════════════════════
-- CANONICAL §7.2 — Role Grants
-- ═══════════════════════════════════════════════════════════════════════
-- Analyst
GRANT INSERT, SELECT ON draft_claim_evidence TO eros_analyst;
GRANT SELECT ON chunks TO eros_analyst;
GRANT SELECT ON artifacts TO eros_analyst;
GRANT SELECT ON runs TO eros_analyst;

-- Verifier
GRANT INSERT, SELECT, UPDATE ON claims TO eros_verifier;
-- DELETE required so claims that fail verification can be removed from the run
-- (their draft_claim_evidence row is set to 'rejected', preserving provenance).
-- Canonical §7.2 omits this; logged as amendment A3 in db/AMENDMENTS.md.
GRANT DELETE ON claims TO eros_verifier;
GRANT INSERT, SELECT, UPDATE ON claim_evidence TO eros_verifier;
GRANT UPDATE (status, promoted_at) ON draft_claim_evidence TO eros_verifier;
GRANT SELECT ON draft_claim_evidence TO eros_verifier;
GRANT SELECT ON chunks TO eros_verifier;
GRANT SELECT ON artifacts TO eros_verifier;
GRANT INSERT, SELECT ON groundedness_kernel_results TO eros_verifier;
-- g05 (attested cross-family) executes with the inserting role's privileges
-- and reads runs.lineage_attestation_status; without this the trigger itself
-- would fail with permission denied. Amendment A4 in db/AMENDMENTS.md.
GRANT SELECT ON runs TO eros_verifier;

-- Reporter
GRANT INSERT, SELECT ON report_sentences TO eros_reporter;
-- Node idempotency (FR13): a resumed/revised report node regenerates its
-- sentence ledger via DELETE + INSERT. Amendment A5 in db/AMENDMENTS.md.
GRANT DELETE ON report_sentences TO eros_reporter;
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

-- Transition table + gate operating point are readable by the workflow roles
GRANT SELECT ON run_status_transitions TO eros_analyst, eros_verifier, eros_reporter,
    eros_ingestor, eros_governor, eros_ui;
GRANT SELECT ON gate_operating_point TO eros_governor, eros_ui;
GRANT INSERT, UPDATE ON gate_operating_point TO eros_governor;
