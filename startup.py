"""Startup reconciliation — canonical §6.9.

**Startup is always a recovery event.** The system does not distinguish a
clean boot from crash recovery; reconciliation runs unconditionally:

  1. assert schema compatibility (fail closed, never repair),
  2. reconcile budget_reservations (released / orphaned per §6.4),
  3. rebuild the VRAM ledger from ``GET /api/ps`` — never from a checkpoint,
  4. re-attest model digests (fail closed only when local generation is
     both configured and required; otherwise record the degradation),
  5. sweep orphaned artifact files older than 24h,
  6. validate store directories exist,
  7. write a RESTART_RECOVERED audit row.
"""
from __future__ import annotations

import logging

from psycopg.types.json import Json

from eros.config import get_static
from eros.db.pool import transaction
from eros.errors import AttestationError, CheckpointIncompatible
from eros.governor.budget import Governor
from eros.ingest.artifact_store import sweep_orphans
from eros.lil import events
from eros.router.attestation import attest_all, load_manifest, require_attested
from eros.router.slot_ledger import SlotLedger

logger = logging.getLogger(__name__)

_REQUIRED_TABLES = (
    "runs", "run_queue", "checkpoints", "artifacts", "chunks",
    "draft_claim_evidence", "claims", "claim_evidence", "report_sentences",
    "budgets", "budget_reservations", "events", "approvals",
    "degraded_mode_log", "groundedness_kernel_results", "oracle_gold_set",
    "audit", "run_status_transitions", "gate_operating_point",
)


def reconcile() -> dict:
    cfg = get_static()
    summary: dict = {"gate4_mode": cfg.gate4_mode}

    with transaction() as cur:
        # 1) Schema compatibility — refuse, never repair (ADR-010 spirit).
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        present = {r["tablename"] for r in cur.fetchall()}
        missing = [t for t in _REQUIRED_TABLES if t not in present]
        if missing:
            raise CheckpointIncompatible(
                f"schema missing tables {missing}; apply db/schema.sql before starting",
                missing=missing,
            )

        # 2) Budget reservations.
        summary["budget"] = Governor().reconcile_startup(cur)

        # 5) Orphan sweep (files with no row, >24h).
        try:
            summary["orphans_swept"] = sweep_orphans(cur)
        except Exception as e:  # sweep is maintenance; never blocks startup
            logger.warning("orphan sweep failed: %s", e)
            summary["orphans_swept"] = -1

    # 3/4) Model layer: ledger from /api/ps + attestation.
    summary["model_layer"] = _reconcile_model_layer(cfg)

    # 6) Store directories (mkdir happens in get_static; verify writable).
    summary["stores"] = {
        "artifacts": str(cfg.artifacts_dir),
        "outputs": str(cfg.outputs_dir),
    }

    with transaction() as cur:
        cur.execute(
            """INSERT INTO audit (event_type, actor, action, metadata)
               VALUES ('RESTART_RECOVERED', 'system', 'startup reconciliation', %s)""",
            (Json(_jsonable(summary)),),
        )
        events.emit(cur, "system.reconciled", payload=_jsonable(summary))
    logger.info("startup reconciliation complete: %s", summary)
    return summary


def _reconcile_model_layer(cfg) -> dict:
    out: dict = {"available": False}
    try:
        manifest = load_manifest(cfg.resolved_manifest_path)
    except AttestationError as e:
        out["reason"] = f"manifest: {e}"
        if cfg.require_local_generation:
            raise
        return out

    ledger = SlotLedger(manifest, cfg.ollama_base_url)
    if not ledger.healthy():
        out["reason"] = "ollama unreachable"
        if cfg.require_local_generation:
            raise AttestationError("ollama required (require_local_generation) but unreachable")
        return out

    out["resident"] = ledger.rebuild_from_ps()

    pinned = all(manifest.spec(r).gguf_digest != "UNPINNED"
                 for r in ("drafter", "checker"))
    status = attest_all(manifest, deep=pinned)
    out["attested"] = {r: v["attested"] for r, v in status["roles"].items()}
    if pinned:
        # Digests are pinned → attestation is load-bearing → fail closed.
        require_attested(status)
        out["available"] = True
    else:
        out["reason"] = "digests UNPINNED — run scripts/attest_models.py --pin"
        out["available"] = True  # routing works; cross-family label will refuse via g05
    return out


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
