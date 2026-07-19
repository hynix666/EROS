"""EROS v3.2 — Negative Test Suite (Gates 1-4, ADR-017, ADR-018, WORM, roles).

§0.1 Executable Specification Mandate: "every gate claimed to hold has a
negative test that *attacks* it and is shown to fail closed."
§12: attacks must produce the canonical error strings. 15/15 must hold.

Requires a live PostgreSQL with db/schema.sql applied. Connection via
EROS_TEST_DSN (default: postgresql://eros:eros@127.0.0.1:5432/eros).
Each test runs in a rolled-back transaction where possible; attacks that
must observe committed state use throwaway rows keyed by uuid.
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest

DSN = os.environ.get("EROS_TEST_DSN", "postgresql://eros:eros@127.0.0.1:5432/eros")

pytestmark = pytest.mark.gates


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def conn():
    with psycopg.connect(DSN) as c:
        yield c
        c.rollback()


@pytest.fixture()
def seeded(conn):
    """A run in 'reporting', one artifact, one chunk — the minimal substrate."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO runs (question, status, budget_envelope, lineage_attestation_status)
               VALUES ('negative-suite', 'reporting', '{}',
                       '{"roles": {"drafter": {"model": "eros-drafter-12k", "family": "llama3", "attested": true},
                                    "checker": {"model": "eros-checker-12k", "family": "qwen2",  "attested": true},
                                    "bad_checker": {"model": "eros-samefam-12k", "family": "llama3", "attested": true},
                                    "unattested": {"model": "eros-ghost-12k", "family": "qwen2", "attested": false}}}')
               RETURNING id""",
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO artifacts (source, hash, snapshot_path)
               VALUES ('negative-suite', %s, '/data/artifacts/negsuite')
               RETURNING id""",
            (uuid.uuid4().hex,),
        )
        artifact_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO chunks (artifact_id, locator, text)
               VALUES (%s, 'p1', 'Acme Corp revenue was 1,500,000,000 dollars on 2024-06-01.')
               RETURNING id""",
            (artifact_id,),
        )
        chunk_id = cur.fetchone()[0]
    return {"run_id": run_id, "chunk_id": chunk_id}


def _claim(cur, run_id, chunk_id, status="draft", kind="deterministic"):
    cur.execute(
        """INSERT INTO claims (run_id, text, status, primary_evidence_chunk_id, verification_kind)
           VALUES (%s, 'Acme Corp revenue was $1.5B.', %s, %s, %s) RETURNING id""",
        (run_id, status, chunk_id, kind),
    )
    return cur.fetchone()[0]


def _support(cur, claim_id, chunk_id, kind="cross-family", model="eros-checker-12k"):
    cur.execute(
        """INSERT INTO claim_evidence (claim_id, chunk_id, relation, checked_by_model, verification_kind, confidence)
           VALUES (%s, %s, 'supports', %s, %s, 0.90)""",
        (claim_id, chunk_id, model, kind),
    )


def _sentence(cur, run_id, ordinal, kind, claim_id=None, template_id=None):
    cur.execute(
        """INSERT INTO report_sentences (run_id, ordinal, text, kind, claim_id, template_id)
           VALUES (%s, %s, 'sentence', %s, %s, %s)""",
        (run_id, ordinal, kind, claim_id, template_id),
    )


def _publish(cur, run_id):
    cur.execute("UPDATE runs SET status = 'evaluating' WHERE id = %s", (run_id,))
    cur.execute("UPDATE runs SET status = 'published' WHERE id = %s", (run_id,))


# ─────────────────────────────────────────────────────────────────────────────
# 1 — Gate 1: commit claim with zero evidence
# ─────────────────────────────────────────────────────────────────────────────
def test_01_gate1_zero_evidence_claim_rejected(conn, seeded):
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException) as ei:
        cur.execute(
            """INSERT INTO claims (run_id, text, status, primary_evidence_chunk_id)
               VALUES (%s, 'unsupported', 'draft', NULL)""",
            (seeded["run_id"],),
        )
    msg = str(ei.value)
    assert msg.startswith("GATE 1:") or "GATE 1:" in msg
    assert "NoEvidenceFound" in msg


# ─────────────────────────────────────────────────────────────────────────────
# 2 — Gate 2: publish run with draft-only evidence
# ─────────────────────────────────────────────────────────────────────────────
def test_02_gate2_publish_with_draft_claims_rejected(conn, seeded):
    with conn.cursor() as cur:
        _claim(cur, seeded["run_id"], seeded["chunk_id"], status="draft")
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _publish(cur, seeded["run_id"])
    assert "GATE 2:" in str(ei.value)
    assert "no verified evidence" in str(ei.value)


# ─────────────────────────────────────────────────────────────────────────────
# 3 — Gate 2 (second face): verified claim but zero supporting evidence rows
# ─────────────────────────────────────────────────────────────────────────────
def test_03_gate2_publish_without_supporting_rows_rejected(conn, seeded):
    with conn.cursor() as cur:
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"], status="verified", kind="cross-family")
        _sentence(cur, seeded["run_id"], 1, "assertive", claim_id=claim)
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _publish(cur, seeded["run_id"])
    assert "GATE 2:" in str(ei.value)


# ─────────────────────────────────────────────────────────────────────────────
# 4 — Gate 3: assertive sentence pointing at a draft claim
# ─────────────────────────────────────────────────────────────────────────────
def test_04_gate3_assertive_sentence_at_draft_claim_rejected(conn, seeded):
    with conn.cursor() as cur:
        good = _claim(cur, seeded["run_id"], seeded["chunk_id"], status="verified", kind="cross-family")
        _support(cur, good, seeded["chunk_id"])
        bad = _claim(cur, seeded["run_id"], seeded["chunk_id"], status="draft")
        _support(cur, bad, seeded["chunk_id"])  # evidence exists, but claim never verified
        # promote bad's evidence so Gate 2 passes and Gate 3 is the one that fires
        cur.execute("UPDATE claims SET status='verified' WHERE id=%s", (bad,))
        cur.execute("UPDATE claims SET status='draft' WHERE id=%s", (bad,))  # back to draft, evidence rows remain
        _sentence(cur, seeded["run_id"], 1, "assertive", claim_id=good)
        _sentence(cur, seeded["run_id"], 2, "assertive", claim_id=bad)
        # Gate 2 blocks drafts too, so flip bad to 'stale' — Gate 2 passes, Gate 3 must still refuse
        cur.execute("UPDATE claims SET status='stale' WHERE id=%s", (bad,))
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _publish(cur, seeded["run_id"])
    assert "GATE 3:" in str(ei.value)
    assert "missing/draft/stale" in str(ei.value)


# ─────────────────────────────────────────────────────────────────────────────
# 5 — Gate 4: Judge scores 0.91 but DGK proved ungrounded
# ─────────────────────────────────────────────────────────────────────────────
def test_05_gate4_ungrounded_claim_blocks_publish(conn, seeded):
    with conn.cursor() as cur:
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"], status="verified", kind="cross-family")
        _support(cur, claim, seeded["chunk_id"])
        cur.execute(
            """INSERT INTO groundedness_kernel_results (claim_id, verdict, missing_numbers)
               VALUES (%s, 'UNGROUNDED', ARRAY['2500000000'])""",
            (claim,),
        )
        _sentence(cur, seeded["run_id"], 1, "assertive", claim_id=claim)
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _publish(cur, seeded["run_id"])
    assert "GATE 4:" in str(ei.value)
    assert "No model vote overrides" in str(ei.value)


# ─────────────────────────────────────────────────────────────────────────────
# 6 — Gate 4 shadow mode: records, does not block (pre-M7 posture)
# ─────────────────────────────────────────────────────────────────────────────
def test_06_gate4_shadow_mode_records_but_does_not_block(conn, seeded):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL eros.gate4_mode = 'shadow'")
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"], status="verified", kind="cross-family")
        _support(cur, claim, seeded["chunk_id"])
        cur.execute(
            "INSERT INTO groundedness_kernel_results (claim_id, verdict) VALUES (%s, 'UNGROUNDED')",
            (claim,),
        )
        _sentence(cur, seeded["run_id"], 1, "assertive", claim_id=claim)
        _publish(cur, seeded["run_id"])  # must NOT raise in shadow mode
        cur.execute("SELECT status FROM runs WHERE id=%s", (seeded["run_id"],))
        assert cur.fetchone()[0] == "published"


# ─────────────────────────────────────────────────────────────────────────────
# 7 — ADR-017: same-family check labelled cross-family
# ─────────────────────────────────────────────────────────────────────────────
def test_07_adr017_same_family_labelled_cross_family_rejected(conn, seeded):
    with conn.cursor() as cur:
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"])
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _support(cur, claim, seeded["chunk_id"], kind="cross-family", model="eros-samefam-12k")
    msg = str(ei.value)
    assert "ATTESTATION:" in msg
    assert "BOTH family llama3" in msg


# ─────────────────────────────────────────────────────────────────────────────
# 8 — ADR-017: unattested checker cannot carry a cross-family label
# ─────────────────────────────────────────────────────────────────────────────
def test_08_adr017_unattested_checker_rejected(conn, seeded):
    with conn.cursor() as cur:
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"])
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _support(cur, claim, seeded["chunk_id"], kind="cross-family", model="eros-ghost-12k")
    assert "no live attestation" in str(ei.value)


# ─────────────────────────────────────────────────────────────────────────────
# 9 — ADR-017: unknown model has no lineage record
# ─────────────────────────────────────────────────────────────────────────────
def test_09_adr017_unknown_model_rejected(conn, seeded):
    with conn.cursor() as cur:
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"])
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _support(cur, claim, seeded["chunk_id"], kind="cross-family", model="mystery-model")
    assert "no lineage record" in str(ei.value)


# ─────────────────────────────────────────────────────────────────────────────
# 10 — ADR-018: infeasible gate target refused by named CHECK constraint
# ─────────────────────────────────────────────────────────────────────────────
def test_10_adr018_infeasible_target_rejected(conn):
    with conn.cursor() as cur, pytest.raises(psycopg.errors.CheckViolation) as ei:
        cur.execute(
            """INSERT INTO gate_operating_point (d_hat, pi_hat, achievable_cost, derived_target, rho_budget)
               VALUES (0.1, 0.2, 1.0, 0.5, 0.35)"""
        )
    assert 'target_must_be_achievable' in str(ei.value)


# ─────────────────────────────────────────────────────────────────────────────
# 11 — WORM: UPDATE / DELETE / TRUNCATE on audit all rejected
# ─────────────────────────────────────────────────────────────────────────────
def test_11_worm_audit_immutable(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO audit (event_type, actor, action) VALUES ('t', 'suite', 'insert')")
        for stmt in (
            "UPDATE audit SET action='x'",
            "DELETE FROM audit",
            "TRUNCATE audit",
        ):
            with pytest.raises(psycopg.errors.RaiseException) as ei:
                cur.execute(stmt)
            assert "WORM violation" in str(ei.value)
            conn.rollback()
            cur.execute("INSERT INTO audit (event_type, actor, action) VALUES ('t', 'suite', 'insert')")


# ─────────────────────────────────────────────────────────────────────────────
# 12 — g00: illegal status transition refused; terminal states have no exits
# ─────────────────────────────────────────────────────────────────────────────
def test_12_g00_illegal_transition_rejected(conn, seeded):
    with conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            cur.execute("UPDATE runs SET status='planning' WHERE id=%s", (seeded["run_id"],))
        assert "GATE 0: illegal run status transition" in str(ei.value)
        conn.rollback()
    # terminal: cancelled → anything is refused
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO runs (question, status, budget_envelope) VALUES ('t','cancelled','{}') RETURNING id"
        )
        rid = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE runs SET status='planning' WHERE id=%s", (rid,))


# ─────────────────────────────────────────────────────────────────────────────
# 13 — Role privilege: the Analyst is structurally unable to write claim_evidence
# ─────────────────────────────────────────────────────────────────────────────
def test_13_analyst_cannot_write_claim_evidence(conn, seeded):
    with conn.cursor() as cur:
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"])
        conn.commit()  # privileges are evaluated against committed objects across SET ROLE
    try:
        with conn.cursor() as cur:
            cur.execute("SET ROLE eros_analyst")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    """INSERT INTO claim_evidence (claim_id, chunk_id, relation, checked_by_model, verification_kind)
                       VALUES (%s, %s, 'supports', 'eros-checker-12k', 'cross-family')""",
                    (claim, seeded["chunk_id"]),
                )
            conn.rollback()
            cur.execute("SET ROLE eros_analyst")
            # Analyst CAN stage drafts (its one write path)
            cur.execute(
                """INSERT INTO draft_claim_evidence (run_id, claim_text, primary_evidence_chunk_id, analyst_model)
                   VALUES (%s, 'staged', %s, 'eros-drafter-12k')""",
                (seeded["run_id"], seeded["chunk_id"]),
            )
            cur.execute("RESET ROLE")
    finally:
        with conn.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute("DELETE FROM runs WHERE id=%s", (seeded["run_id"],))
            conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 14 — Promotion path: Verifier promotes a draft; claims row appears as 'draft'
# ─────────────────────────────────────────────────────────────────────────────
def test_14_promotion_path(conn, seeded):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO draft_claim_evidence (run_id, claim_text, primary_evidence_chunk_id, confidence, analyst_model)
               VALUES (%s, 'promoted claim', %s, 0.80, 'eros-drafter-12k') RETURNING id""",
            (seeded["run_id"], seeded["chunk_id"]),
        )
        draft_id = cur.fetchone()[0]
        cur.execute("UPDATE draft_claim_evidence SET status='promoted' WHERE id=%s", (draft_id,))
        cur.execute(
            "SELECT status, promoted_at FROM draft_claim_evidence WHERE id=%s", (draft_id,)
        )
        status, promoted_at = cur.fetchone()
        assert status == "promoted" and promoted_at is not None
        cur.execute(
            "SELECT count(*) FROM claims WHERE run_id=%s AND text='promoted claim' AND status='draft'",
            (seeded["run_id"],),
        )
        assert cur.fetchone()[0] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 15 — Happy path: all gates satisfied → publish succeeds
# ─────────────────────────────────────────────────────────────────────────────
def test_15_happy_path_publishes(conn, seeded):
    with conn.cursor() as cur:
        claim = _claim(cur, seeded["run_id"], seeded["chunk_id"], status="verified", kind="cross-family")
        _support(cur, claim, seeded["chunk_id"], kind="cross-family", model="eros-checker-12k")
        cur.execute(
            "INSERT INTO groundedness_kernel_results (claim_id, verdict) VALUES (%s, 'INDETERMINATE')",
            (claim,),
        )
        _sentence(cur, seeded["run_id"], 1, "structural", template_id="tpl.header")
        _sentence(cur, seeded["run_id"], 2, "assertive", claim_id=claim)
        _sentence(cur, seeded["run_id"], 3, "disclosure", template_id="tpl.degradation_footer")
        _publish(cur, seeded["run_id"])
        cur.execute("SELECT status FROM runs WHERE id=%s", (seeded["run_id"],))
        assert cur.fetchone()[0] == "published"
