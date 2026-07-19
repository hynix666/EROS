"""Sequential Slot Ledger — canonical ADR-009 / §6.6 / Appendix B.

Exactly two slots:
    GENERATION : drafter XOR checker
    ON_DEMAND  : arbiter XOR judge

Platform ground truth this design answers (Appendix B): Ollama pre-allocates
the full KV cache at load; num_ctx cannot change per-request; the only lever
is unload. So residency is governed, never saturated: every transition is
**evict-then-load under one GPU mutex** (a Postgres transaction advisory
lock), reconciled against ``GET /api/ps`` before and after, and the ledger
is **rebuilt from /api/ps at startup — never from a checkpoint** (keep_alive
does not survive restarts).

Divergence between the ledger's expectation and /api/ps pages immediately
(§10): a CRITICAL log plus a ``vram_ledger_divergence`` event.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx
import psycopg

from eros.db.pool import gpu_mutex
from eros.errors import VramSlotUnavailable
from eros.lil import events
from eros.router.attestation import Manifest, Role

logger = logging.getLogger(__name__)

Slot = Literal["generation", "on_demand"]
SLOT_OF_ROLE: dict[Role, Slot] = {
    "drafter": "generation", "checker": "generation",
    "arbiter": "on_demand", "judge": "on_demand",
}


@dataclass
class SlotLedger:
    manifest: Manifest
    base_url: str
    expected: dict[Slot, str | None] = field(default_factory=lambda: {"generation": None, "on_demand": None})
    _client: httpx.Client | None = None

    # ── Ollama plumbing ────────────────────────────────────────────────────
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(10.0, read=180.0))
        return self._client

    def ps(self) -> list[dict]:
        r = self.client.get("/api/ps", timeout=5.0)
        r.raise_for_status()
        return r.json().get("models", [])

    def resident_tags(self) -> set[str]:
        return {m.get("name", "").split(":")[0] + (":" + m["name"].split(":", 1)[1] if ":" in m.get("name", "") else "")
                for m in self.ps()} - {""}

    def healthy(self) -> bool:
        try:
            return self.client.get("/api/version", timeout=3.0).status_code == 200
        except httpx.HTTPError:
            return False

    # ── Startup: rebuild from /api/ps, never from checkpoint ───────────────
    def rebuild_from_ps(self) -> dict[Slot, str | None]:
        self.expected = {"generation": None, "on_demand": None}
        try:
            resident = self.resident_tags()
        except httpx.HTTPError:
            return self.expected  # Ollama down — router degrades; caller logs
        for role in SLOT_OF_ROLE:
            tag = self.manifest.spec(role).modelfile_tag
            if any(r == tag or r.startswith(tag + ":") or r.split(":")[0] == tag for r in resident):
                self.expected[SLOT_OF_ROLE[role]] = tag
        return dict(self.expected)

    # ── The one transition primitive: evict-then-load under the mutex ─────
    def ensure_loaded(self, cur: psycopg.Cursor, role: Role, *, run_id: str | None = None) -> dict:
        tag = self.manifest.spec(role).modelfile_tag
        slot = SLOT_OF_ROLE[role]
        transition: dict = {"slot": slot, "role": role, "to": tag, "at": time.time()}

        with gpu_mutex(cur):
            resident = self._resident_or_raise()
            if self._tag_resident(tag, resident):
                self.expected[slot] = tag
                transition["action"] = "noop"
                self._reconcile(cur, run_id)
                return transition

            occupant = self._slot_occupant(slot, resident)
            if occupant is not None:
                transition["evicted"] = occupant
                self._evict(occupant)

            self._load(tag)
            self.expected[slot] = tag
            transition["action"] = "evict_then_load" if occupant else "load"
            self._reconcile(cur, run_id)

        events.emit(cur, "slot.transition", run_id=run_id, payload=transition, model_name=tag)
        return transition

    # ── internals ──────────────────────────────────────────────────────────
    def _resident_or_raise(self) -> set[str]:
        try:
            return self.resident_tags()
        except httpx.HTTPError as e:
            raise VramSlotUnavailable("ollama /api/ps unreachable", cause=str(e)) from e

    @staticmethod
    def _tag_resident(tag: str, resident: set[str]) -> bool:
        return any(r == tag or r.split(":")[0] == tag for r in resident)

    def _slot_occupant(self, slot: Slot, resident: set[str]) -> str | None:
        for role, s in SLOT_OF_ROLE.items():
            if s != slot:
                continue
            t = self.manifest.spec(role).modelfile_tag
            for r in resident:
                if r == t or r.split(":")[0] == t:
                    return r
        return None

    def _evict(self, tag: str, timeout: float = 60.0) -> None:
        # keep_alive: 0 unloads after the (empty) request completes.
        self.client.post("/api/generate", json={"model": tag, "keep_alive": 0})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._tag_resident(tag, self._resident_or_raise()):
                return
            time.sleep(0.5)
        raise VramSlotUnavailable(f"evict of {tag} did not complete within {timeout}s")

    def _load(self, tag: str, timeout: float = 300.0) -> None:
        # An empty generate loads the model; keep_alive bounded — never pin
        # (Appendix B fact 4: a pinned model blocks incoming loads).
        r = self.client.post("/api/generate",
                             json={"model": tag, "prompt": "", "keep_alive": "10m"},
                             timeout=httpx.Timeout(10.0, read=timeout))
        r.raise_for_status()
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self._tag_resident(tag, self._resident_or_raise()):
                return
            time.sleep(0.5)
        raise VramSlotUnavailable(f"load of {tag} not visible in /api/ps")

    def _reconcile(self, cur: psycopg.Cursor, run_id: str | None) -> None:
        """Expected residency vs /api/ps. Any divergence pages (§10)."""
        resident = self._resident_or_raise()
        expected_tags = {t for t in self.expected.values() if t}
        unexpected = {r for r in resident
                      if not any(r == t or r.split(":")[0] == t for t in expected_tags)}
        missing = {t for t in expected_tags if not self._tag_resident(t, resident)}
        if unexpected or missing or len(resident) > 2:
            payload = {"unexpected": sorted(unexpected), "missing": sorted(missing),
                       "resident_count": len(resident)}
            logger.critical("vram_ledger_divergence: %s", payload)
            events.emit(cur, "vram_ledger_divergence", run_id=run_id, payload=payload)
