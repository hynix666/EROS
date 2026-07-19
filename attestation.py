"""Lineage registry & runtime attestation — canonical §6.6 / ADR-022 / ADR-017.

The model manifest (config/manifest.json) pins each role (drafter, checker,
arbiter, judge) to a family, a derived Modelfile tag, a context ceiling, and
a SHA-256 GGUF digest. Attestation resolves the *resident* tag to its GGUF
blob through Ollama's own manifest store and streams a SHA-256 over the
bytes Ollama will actually load. The result is written to
``runs.lineage_attestation_status`` in the shape the g05 trigger enforces:

    {"roles": {"drafter": {"model": "eros-drafter-12k", "family": "llama3",
                            "attested": true, "digest": "sha256:...", ...}, ...}}

Mismatch → fail closed (AttestationError) in startup contexts; mid-run the
caller emits LINEAGE_ATTESTATION_FAILED and alerts (§10).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from eros.errors import AttestationError

Role = Literal["drafter", "checker", "arbiter", "judge"]
ROLES: tuple[Role, ...] = ("drafter", "checker", "arbiter", "judge")


class ModelSpec(BaseModel):
    family: str
    variant: str
    quantization: str = "q8_0"
    num_ctx: int = Field(gt=0)
    gguf_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$|^UNPINNED$")
    modelfile_tag: str


class Manifest(BaseModel):
    version: str
    updated_at: str
    updated_by: str
    adr_ref: str
    models: dict[Role, ModelSpec]

    def spec(self, role: Role) -> ModelSpec:
        try:
            return self.models[role]
        except KeyError as e:  # pragma: no cover - schema requires all four
            raise AttestationError(f"manifest missing role {role!r}") from e


def load_manifest(path: Path) -> Manifest:
    """Fail-closed at config time (§6.6): an invalid manifest never routes."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        m = Manifest.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as e:
        raise AttestationError(f"model manifest invalid or unreadable: {path}", cause=str(e)) from e
    missing = [r for r in ROLES if r not in m.models]
    if missing:
        raise AttestationError(f"model manifest missing roles: {missing}")
    fams = {r: m.models[r].family for r in ("drafter", "checker")}
    if fams["drafter"] == fams["checker"]:
        raise AttestationError(
            "manifest violates cross-family pinning: drafter and checker share family "
            f"{fams['drafter']!r} (ADR-005)"
        )
    for r, spec in m.models.items():
        if not spec.modelfile_tag.startswith("eros-"):
            raise AttestationError(
                f"role {r}: tag {spec.modelfile_tag!r} is not a derived Modelfile tag; "
                "base tags are blocked (§6.6 Context Ceiling)"
            )
    return m


# ── Ollama store resolution (cross-platform) ────────────────────────────────
def ollama_models_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env)
    # Ollama uses ~/.ollama/models on Linux/macOS and %USERPROFILE%\.ollama\models
    # on Windows — Path.home() covers all three.
    return Path.home() / ".ollama" / "models"


def _iter_tag_manifests(models_dir: Path):
    root = models_dir / "manifests"
    if not root.is_dir():
        return
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def resolve_tag_gguf_digest(tag: str, models_dir: Path | None = None) -> str | None:
    """Find the GGUF model-layer digest Ollama associates with a local tag."""
    models_dir = models_dir or ollama_models_dir()
    name, _, version = tag.partition(":")
    version = version or "latest"
    for mf in _iter_tag_manifests(models_dir):
        if mf.parent.name != name or mf.name != version:
            continue
        try:
            doc = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for layer in doc.get("layers", []):
            if layer.get("mediaType", "").endswith("image.model"):
                return layer.get("digest")
    return None


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return "sha256:" + h.hexdigest()


@dataclass(frozen=True)
class Attestation:
    role: Role
    model: str
    family: str
    attested: bool
    digest: str
    reason: str | None = None


def attest_role(manifest: Manifest, role: Role, *, deep: bool = True,
                models_dir: Path | None = None) -> Attestation:
    spec = manifest.spec(role)
    tag = spec.modelfile_tag
    if spec.gguf_digest == "UNPINNED":
        return Attestation(role, tag, spec.family, False, "UNPINNED",
                           "digest not yet pinned — run scripts/attest_models.py --pin")
    resolved = resolve_tag_gguf_digest(tag, models_dir)
    if resolved is None:
        return Attestation(role, tag, spec.family, False, spec.gguf_digest,
                           f"tag {tag!r} not found in Ollama store")
    if resolved != spec.gguf_digest:
        return Attestation(role, tag, spec.family, False, resolved,
                           f"resident digest {resolved} != pinned {spec.gguf_digest}")
    if deep:
        blob = (models_dir or ollama_models_dir()) / "blobs" / resolved.replace(":", "-")
        if not blob.is_file():
            return Attestation(role, tag, spec.family, False, resolved, "blob file missing")
        actual = sha256_file(blob)
        if actual != resolved:
            return Attestation(role, tag, spec.family, False, actual,
                               "blob bytes do not hash to their digest (corruption/tamper)")
    return Attestation(role, tag, spec.family, True, spec.gguf_digest)


def attest_all(manifest: Manifest, *, deep: bool = True,
               models_dir: Path | None = None) -> dict:
    """Build the runs.lineage_attestation_status document the g05 trigger reads."""
    roles: dict[str, dict] = {}
    for role in ROLES:
        a = attest_role(manifest, role, deep=deep, models_dir=models_dir)
        entry: dict = {
            "model": a.model, "family": a.family, "attested": a.attested,
            "digest": a.digest, "checked_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.system().lower(),
        }
        if a.reason:
            entry["reason"] = a.reason
        roles[role] = entry
    return {"roles": roles}


def require_attested(status: dict, roles: tuple[Role, ...] = ("drafter", "checker")) -> None:
    """Fail-closed check for startup / resume re-attestation (§6.9)."""
    bad = [r for r in roles
           if not status.get("roles", {}).get(r, {}).get("attested", False)]
    if bad:
        reasons = {r: status["roles"].get(r, {}).get("reason", "unknown") for r in bad}
        raise AttestationError(f"lineage attestation failed for roles {bad}", reasons=reasons)
