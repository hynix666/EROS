"""RunState v4 — canonical §6.3 / ADR-010.

UUID references only (never embedded objects) so checkpoints stay lean.
Trust telemetry (``lineage_attestation_status``) is carried and is **never
defaulted**: a checkpoint that cannot faithfully present it is refused
(CheckpointIncompatible), not repaired.
"""
from __future__ import annotations

from typing import Any, TypedDict

from eros.errors import CheckpointIncompatible

RUNSTATE_VERSION = 4


class RunState(TypedDict, total=False):
    runstate_version: int
    run_id: str
    question: str
    sensitivity: str
    gate_class: str
    envelope: dict
    model_mode: bool                      # local/external inference available this run
    plan: list[str]
    sources: list[dict]                   # {url,title,connector} — pre-artifact metadata
    artifact_ids: list[str]               # UUID refs (canonical)
    chunk_ids: list[str]                  # UUID refs
    replans_used: int
    revisions_used: int
    degradations: list[str]               # rendered verbatim into the report footer
    lineage_attestation_status: dict      # trust telemetry — never defaulted
    provenance: dict
    route: str                            # conditional-edge signal: ok|replan|insufficient|revise|paused|cancelled


def initial_state(*, run_id: str, question: str, sensitivity: str,
                  gate_class: str, envelope: dict, model_mode: bool,
                  lineage: dict, provenance: dict) -> RunState:
    return RunState(
        runstate_version=RUNSTATE_VERSION,
        run_id=run_id,
        question=question,
        sensitivity=sensitivity,
        gate_class=gate_class,
        envelope=envelope,
        model_mode=model_mode,
        plan=[],
        sources=[],
        artifact_ids=[],
        chunk_ids=[],
        replans_used=0,
        revisions_used=0,
        degradations=[],
        lineage_attestation_status=lineage,
        provenance=provenance,
        route="ok",
    )


def validate_loaded(state: dict[str, Any]) -> RunState:
    """ADR-010: forward-only, fail closed. Trust fields are never defaulted."""
    version = state.get("runstate_version")
    if version != RUNSTATE_VERSION:
        raise CheckpointIncompatible(
            f"checkpoint runstate_version {version!r} != {RUNSTATE_VERSION}; "
            "no registered migration — refusing to resume (evidence preserved)",
            found=version,
        )
    if "lineage_attestation_status" not in state or state["lineage_attestation_status"] is None:
        raise CheckpointIncompatible(
            "checkpoint lacks lineage_attestation_status; trust telemetry is never defaulted"
        )
    return state  # type: ignore[return-value]
