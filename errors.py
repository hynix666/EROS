"""EROS error taxonomy — canonical §6.11.

ErosException (base)
├── ArtifactError          → quarantine, audit, continue run, log gap
├── ResourceError          → ContextCeilingExceeded | BudgetReservationFailed
│                            | VramSlotUnavailable | StorageQuotaExceeded
├── EvidenceError          → NoEvidenceFound | VerificationInconclusive
├── AttestationError       → fail closed; human review required
├── SensitivityError       → fail closed; never route external
├── CheckpointIncompatible → refuse to resume; evidence preserved
└── LlmOutputMalformed     → structural-correction retry (×2) → re-plan → human gate

DegradedModeDetected is the FR18 control signal the LIL returns instead of
silently queuing a CPU fallback: the user must explicitly choose
"Proceed in Degraded Mode" or "Abort".
"""
from __future__ import annotations


class ErosException(Exception):
    """Base of the canonical taxonomy. Carries structured context."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


# ── Artifact plane ──────────────────────────────────────────────────────────
class ArtifactError(ErosException):
    """Quarantine the artifact, audit, continue the run, log the gap."""


# ── Resource plane ──────────────────────────────────────────────────────────
class ResourceError(ErosException):
    pass


class ContextCeilingExceeded(ResourceError):
    """Prompt exceeds the role's derived-Modelfile context. Never silently truncate."""


class BudgetReservationFailed(ResourceError):
    """Atomic reserve returned zero rows — ceiling would be breached."""


class VramSlotUnavailable(ResourceError):
    """Slot occupied by an in-flight sibling; queue, never partial-load."""


class StorageQuotaExceeded(ResourceError):
    """A store hit its 95% watermark; the run pauses (paused_storage)."""


# ── Evidence plane ──────────────────────────────────────────────────────────
class EvidenceError(ErosException):
    pass


class NoEvidenceFound(EvidenceError):
    """Evidence Sufficiency Gate: < 3 chunks above threshold (FR9)."""


class VerificationInconclusive(EvidenceError):
    pass


# ── Trust plane (fail closed) ───────────────────────────────────────────────
class AttestationError(ErosException):
    """Resident weights ≠ registry digest, or manifest invalid. Human review."""


class SensitivityError(ErosException):
    """A routing decision would violate C1. Never route external."""


class CheckpointIncompatible(ErosException):
    """Checkpoint cannot be faithfully read/migrated. Refuse; evidence preserved."""


class LlmOutputMalformed(ErosException):
    """Structured output failed to parse after structural-correction retries."""


# ── FR18 control signal ─────────────────────────────────────────────────────
class DegradedModeDetected(ErosException):
    """Local GPU acceleration unavailable at run start. The LIL surfaces an
    explicit fast-abort choice; it never silently defaults to a 3-hour
    CPU fallback."""

    def __init__(self, message: str, *, estimated_minutes: int = 180, **context: object) -> None:
        super().__init__(message, **context)
        self.estimated_minutes = estimated_minutes
