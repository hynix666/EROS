"""EROS configuration — canonical §6.10.

Two classes:
  * StaticConfig  — trust-relevant; resolved once at startup, then immutable.
                    Its SHA-256 digest goes into runs.provenance so no run's
                    guarantees can silently change under it.
  * DynamicConfig — runtime-tunable (log level, ef_search, trace sampling);
                    changes are audited and bump the config digest.

All values are environment-driven (EROS_* variables, .env supported) with
validated defaults. Paths default under EROS_DATA_DIR so a single variable
relocates the whole store layout, cross-platform.
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Sensitivity = Literal["open", "restricted", "sensitive"]


def _default_data_dir() -> Path:
    return Path(os.environ.get("EROS_DATA_DIR", str(Path.home() / ".eros" / "data")))


class StaticConfig(BaseSettings):
    """Immutable after startup (§6.10). Trust configuration lives here."""

    model_config = SettingsConfigDict(env_prefix="EROS_", env_file=".env", extra="ignore", frozen=True)

    # ── Datastore (ADR-003: PostgreSQL monostore) ──────────────────────────
    dsn: str = "postgresql://eros:eros@127.0.0.1:5432/eros"
    embedding_dim: int = 1024  # bge-large-en-v1.5

    # ── Store layout (FR11 quota roots) ────────────────────────────────────
    data_dir: Path = Field(default_factory=_default_data_dir)

    # ── Trust chain ────────────────────────────────────────────────────────
    # Gate 4 arms 'blocking' only after M7 < 2% on the Gold Set (§6.8.1);
    # ships in 'shadow' (record, do not block) until measured.
    gate4_mode: Literal["blocking", "shadow"] = "shadow"
    sensitivity_default: Sensitivity = "open"

    # ── Model layer ────────────────────────────────────────────────────────
    ollama_base_url: str = "http://127.0.0.1:11434"
    manifest_path: Path | None = None          # defaults to <repo>/config/manifest.json
    llamacpp_server_url: str | None = None     # CPU fallback rung, if operated
    # External escalation (FR5): ZDR is a hard admission requirement.
    external_enabled: bool = False
    anthropic_zdr_confirmed: bool = False
    openai_zdr_confirmed: bool = False
    require_local_generation: bool = False     # FR18 posture: True → probe GPU at run start

    # ── Budget Governor (§6.4) ─────────────────────────────────────────────
    budget_ceiling_usd: float = 50.00

    # ── Heuristic Gate (§6.2) ──────────────────────────────────────────────
    gate_confidence_floor: float = 0.70        # below → full_investigation

    # ── Pipeline bounds ────────────────────────────────────────────────────
    max_replans: int = 2                       # Planner: at most two re-plans
    max_sources_per_run: int = 12
    evidence_min_chunks: int = 3               # Evidence Sufficiency Gate
    max_qa_revisions: int = 1

    # ── Quota watermarks (§6.10) ───────────────────────────────────────────
    quota_warn: float = 0.80
    quota_alert: float = 0.90
    quota_act: float = 0.95

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def resolved_manifest_path(self) -> Path:
        if self.manifest_path is not None:
            return self.manifest_path
        # repo layout: backend/eros/config.py → repo root two levels up
        return Path(__file__).resolve().parents[2] / "config" / "manifest.json"

    @model_validator(mode="after")
    def _validate(self) -> "StaticConfig":
        if not (0.0 < self.gate_confidence_floor <= 1.0):
            raise ValueError("gate_confidence_floor must be in (0, 1]")
        if self.budget_ceiling_usd < 0:
            raise ValueError("budget_ceiling_usd must be >= 0")
        if not (0 < self.quota_warn < self.quota_alert < self.quota_act <= 1.0):
            raise ValueError("quota watermarks must satisfy warn < alert < act <= 1.0")
        return self

    def digest(self) -> str:
        """SHA-256 over the static configuration → runs.provenance.config_digest."""
        payload = self.model_dump(mode="json")
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def conninfo(self) -> str:
        """DSN with the Gate-4 GUC applied to every session, so the trigger's
        posture is configuration set once at the boundary, not per-call.

        Handles both DSN forms: URI (``postgresql://…``) takes ``options`` as
        a percent-encoded query parameter; keyword form takes the quoted
        ``options='…'`` keyword.
        """
        from urllib.parse import quote

        opt = f"-c eros.gate4_mode={self.gate4_mode}"
        if self.dsn.startswith(("postgresql://", "postgres://")):
            sep = "&" if "?" in self.dsn else "?"
            return f"{self.dsn}{sep}options={quote(opt, safe='')}"
        return f"{self.dsn} options='{opt}'"


class DynamicConfig(BaseSettings):
    """Runtime-tunable (§6.10). Changes are audited by the caller."""

    model_config = SettingsConfigDict(env_prefix="EROS_DYN_", extra="ignore")

    log_level: str = "INFO"
    hnsw_ef_search: int = 64
    trace_sample_rate: float = 1.0


@lru_cache(maxsize=1)
def get_static() -> StaticConfig:
    cfg = StaticConfig()
    cfg.artifacts_dir.mkdir(parents=True, exist_ok=True)
    cfg.outputs_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def get_dynamic() -> DynamicConfig:
    return DynamicConfig()
