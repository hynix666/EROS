"""Core invariants — chunker, artifact write ordering, Governor, C11, ADR-010.

DB-backed tests use a live PostgreSQL (EROS_TEST_DSN) with db/schema.sql
applied; isolation via throwaway rows and per-test budget periods.
"""
from __future__ import annotations

import importlib
import inspect
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from eros.errors import BudgetReservationFailed, CheckpointIncompatible
from eros.governor.budget import Governor, idempotency_key
from eros.ingest.artifact_store import store_artifact
from eros.ingest.processing import CHUNK_OVERLAP, CHUNK_TOKENS, chunk_text
from eros.pipeline.state import RUNSTATE_VERSION, validate_loaded

pytestmark = pytest.mark.core

DSN = os.environ.get("EROS_TEST_DSN", "postgresql://eros:eros@127.0.0.1:5432/eros")


@pytest.fixture()
def conn():
    with psycopg.connect(DSN, row_factory=dict_row) as c:
        yield c
        c.rollback()


@pytest.fixture()
def run_id(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO runs (question, status, budget_envelope) "
            "VALUES ('core-suite', 'planning', %s) RETURNING id", (Json({}),))
        return str(cur.fetchone()["id"])


# ── Module imports (syntax/import-time smoke) ───────────────────────────────
def test_all_modules_import():
    for mod in [
        "eros.config", "eros.errors", "eros.db.pool", "eros.lil.events",
        "eros.governor.budget", "eros.router.attestation", "eros.router.slot_ledger",
        "eros.router.router", "eros.gate.heuristic", "eros.ingest.artifact_store",
        "eros.ingest.processing", "eros.ingest.connectors", "eros.retrieval.hybrid",
        "eros.dgk.kernel", "eros.pipeline.state", "eros.pipeline.nodes",
        "eros.pipeline.graph", "eros.recovery.startup", "eros.lil.app",
        "eros.mcp_server",
    ]:
        importlib.import_module(mod)


# ── Chunker (§6.5.1: 512 target, 20% overlap) ──────────────────────────────
def test_chunker_target_and_overlap():
    words = [f"w{i}" for i in range(3000)]
    text = ". ".join(" ".join(words[i:i + 15]) for i in range(0, 3000, 15))
    chunks = chunk_text(text)
    assert len(chunks) >= 4
    for c in chunks:
        assert len(c.split()) <= int(CHUNK_TOKENS * 1.3), "chunk far above target"
    for a, b in zip(chunks, chunks[1:]):
        tail = set(a.split()[-CHUNK_OVERLAP:])
        head = set(b.split()[:CHUNK_OVERLAP])
        assert tail & head, "consecutive chunks must share overlap tokens"


def test_chunker_empty_and_short():
    assert chunk_text("") == []
    assert chunk_text("one short paragraph.") == ["one short paragraph."]


# ── Artifact write ordering (§6.5 / §10) ────────────────────────────────────
class _CrashOnInsert:
    """Cursor proxy that crashes on the artifacts INSERT — simulating a crash
    after rename() but before the row commit."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, params=None):
        if "INSERT INTO artifacts" in sql:
            raise RuntimeError("simulated crash between rename and commit")
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_artifact_ordering_crash_leaves_orphan_never_dangling_pointer(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("EROS_DATA_DIR", str(tmp_path))
    import eros.config as config
    import eros.ingest.artifact_store as astore
    config.get_static.cache_clear()
    # Quota is orthogonal to write ordering (and this sandbox disk is near
    # full); it gets its own targeted test below.
    monkeypatch.setattr(astore, "_check_quota", lambda root: None)
    content = f"ordering-proof {uuid.uuid4()}".encode()

    with conn.cursor() as cur:
        with pytest.raises(RuntimeError, match="simulated crash"):
            store_artifact(_CrashOnInsert(cur), content=content, source="test")
    conn.rollback()

    # Bytes are durable on disk (orphan)…
    files = [p for p in (tmp_path / "artifacts").rglob("*") if p.is_file()]
    assert files, "file must exist after crash-before-commit (orphan by design)"
    # …and no row points at them (dangling snapshot_path structurally impossible).
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM artifacts WHERE source = 'test'")
        assert cur.fetchone() is None

    # Re-execution after 'restart' is idempotent and completes cleanly (FR13).
    with conn.cursor() as cur:
        aid1, created1 = store_artifact(cur, content=content, source="test")
        aid2, created2 = store_artifact(cur, content=content, source="test")
    assert created1 is True and created2 is False and aid1 == aid2
    config.get_static.cache_clear()


# ── Budget Governor (§6.4) ─────────────────────────────────────────────────
def test_budget_reserve_settle_refuse_and_idempotency(conn, run_id):
    g = Governor(ceiling_usd=50.0)
    period = f"test-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        r1 = g.reserve(cur, run_id=run_id, node_name="n1",
                       idem_key=idempotency_key(run_id, "n1", 1, "p"),
                       estimated_cost=40.0, provider="test", period=period)
        assert r1.status == "reserved"

        # Ceiling holds: 40 held + 20 requested > 50 → refused, zero rows.
        with pytest.raises(BudgetReservationFailed):
            g.reserve(cur, run_id=run_id, node_name="n2",
                      idem_key=idempotency_key(run_id, "n2", 1, "p"),
                      estimated_cost=20.0, provider="test", period=period)

        # Settle at 5 → 35 refunded synchronously; 40 now fits (5 + 40 ≤ 50).
        g.settle(cur, r1.id, 5.0)
        r3 = g.reserve(cur, run_id=run_id, node_name="n3",
                       idem_key=idempotency_key(run_id, "n3", 1, "p"),
                       estimated_cost=40.0, provider="test", period=period)
        assert r3.status == "reserved"

        # Idempotent on key: the resumed node gets its existing reservation.
        again = g.reserve(cur, run_id=run_id, node_name="n1",
                          idem_key=idempotency_key(run_id, "n1", 1, "p"),
                          estimated_cost=40.0, provider="test", period=period)
        assert again.id == r1.id and again.status == "settled"


def test_budget_startup_reconciliation(conn, run_id):
    g = Governor(ceiling_usd=50.0)
    period = f"test-{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        undispatched = g.reserve(cur, run_id=run_id, node_name="a",
                                 idem_key=idempotency_key(run_id, "a", 1, "p"),
                                 estimated_cost=1.0, provider="test", period=period)
        dispatched = g.reserve(cur, run_id=run_id, node_name="b",
                               idem_key=idempotency_key(run_id, "b", 1, "p"),
                               estimated_cost=1.0, provider="test", period=period)
        g.record_dispatch(cur, dispatched.id, "req-123")

        out = g.reconcile_startup(cur)
        assert out["released_undispatched"] >= 1 and out["orphaned"] >= 1
        cur.execute("SELECT status FROM budget_reservations WHERE id = %s",
                    (undispatched.id,))
        assert cur.fetchone()["status"] == "released"
        cur.execute("SELECT status FROM budget_reservations WHERE id = %s",
                    (dispatched.id,))
        assert cur.fetchone()["status"] == "orphaned"


# ── C11: trust never influences retrieval ───────────────────────────────────
def test_c11_trust_seed_absent_from_retrieval():
    import eros.retrieval.hybrid as hybrid
    src = inspect.getsource(hybrid)
    body = "\n".join(l for l in src.split('"""', 2)[-1].splitlines())  # skip docstring
    assert "trust_seed" not in body, "C11 violated: retrieval references trust_seed"


# ── ADR-010: checkpoints fail closed, trust fields never defaulted ─────────
def test_state_validation_fails_closed():
    with pytest.raises(CheckpointIncompatible):
        validate_loaded({"runstate_version": 3, "lineage_attestation_status": {}})
    with pytest.raises(CheckpointIncompatible):
        validate_loaded({"runstate_version": RUNSTATE_VERSION})
    ok = validate_loaded({"runstate_version": RUNSTATE_VERSION,
                          "lineage_attestation_status": {"roles": {}}})
    assert ok["runstate_version"] == RUNSTATE_VERSION


def test_quota_act_watermark_pauses(monkeypatch, tmp_path):
    """FR11/§6.10: at the 95% act watermark the artifacts store refuses writes."""
    import collections
    import eros.ingest.artifact_store as astore

    fake = collections.namedtuple("sv", "f_bavail f_blocks")(f_bavail=2, f_blocks=100)
    monkeypatch.setattr(astore.os, "statvfs", lambda p: fake, raising=False)
    from eros.errors import StorageQuotaExceeded
    with pytest.raises(StorageQuotaExceeded):
        astore._check_quota(tmp_path)
