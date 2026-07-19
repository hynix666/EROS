"""Model Router — canonical §6.6 / ADR-004 / ADR-005 / FR5.

Rule order (hard, in code order): **Sensitivity → Lineage → Slot
Availability → Task/Cost/Latency/Health.** Sensitivity is evaluated first
and structurally: for anything not 'open', the external rungs of the
fallback chain are never even constructed (C1: sensitive content never
leaves the machine).

Fallback chain: Ollama (primary) → llama.cpp CPU (if operated) →
Anthropic → OpenAI. External providers require **ZDR confirmed in static
config** (hard admission requirement), a successful atomic budget
reservation, and an idempotency key written **before** dispatch (§6.9) so
a crash mid-call can never double-bill.

Nodes never name a model — they request inference by task type; lineage is
resolved through the manifest, and base tags are structurally impossible
(the manifest loader rejects non-derived tags).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass

import httpx
import psycopg
from psycopg.types.json import Json

from eros.config import StaticConfig
from eros.errors import (
    ContextCeilingExceeded,
    ErosException,
    SensitivityError,
)
from eros.governor.budget import Governor, idempotency_key, prompt_digest
from eros.lil import events
from eros.router.attestation import Manifest, Role, attest_all
from eros.router.slot_ledger import SlotLedger

logger = logging.getLogger(__name__)

TASK_ROLE: dict[str, Role] = {
    "draft": "drafter",
    "verify": "checker",
    "adjudicate": "arbiter",
    "judge": "judge",
}

# [assumption] External pricing, USD per 1M tokens (in, out). Used only for
# reservation estimates; actuals settle from usage fields. Override via env.
EXTERNAL_PRICING = {
    "anthropic": (float(os.environ.get("EROS_PRICE_ANTHROPIC_IN", 3.0)),
                  float(os.environ.get("EROS_PRICE_ANTHROPIC_OUT", 15.0))),
    "openai": (float(os.environ.get("EROS_PRICE_OPENAI_IN", 2.5)),
               float(os.environ.get("EROS_PRICE_OPENAI_OUT", 10.0))),
}


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    provider: str            # ollama | llamacpp | anthropic | openai
    tokens_in: int
    tokens_out: int
    latency_ms: int


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ModelRouter:
    def __init__(self, cfg: StaticConfig, manifest: Manifest, ledger: SlotLedger,
                 governor: Governor) -> None:
        self.cfg = cfg
        self.manifest = manifest
        self.ledger = ledger
        self.governor = governor

    # ── FR18 probe: is local accelerated generation available? ─────────────
    def probe_local_generation(self) -> tuple[bool, str]:
        if not self.ledger.healthy():
            return False, "ollama unreachable"
        try:
            tags = {m.get("name", "") for m in
                    self.ledger.client.get("/api/tags", timeout=5.0).json().get("models", [])}
        except httpx.HTTPError as e:
            return False, f"ollama /api/tags failed: {e}"
        drafter = self.manifest.spec("drafter").modelfile_tag
        if not any(t == drafter or t.split(":")[0] == drafter for t in tags):
            return False, f"derived model {drafter!r} not created (run scripts/build_modelfiles.py)"
        return True, "ok"

    # ── Lineage recording: written BEFORE any verification writes (g05) ────
    def record_lineage(self, cur: psycopg.Cursor, run_id: str, *, deep: bool = False) -> dict:
        status = attest_all(self.manifest, deep=deep)
        cur.execute(
            "UPDATE runs SET lineage_attestation_status = %s WHERE id = %s",
            (Json(status), run_id),
        )
        events.emit(cur, "lineage.recorded", run_id=run_id,
                    payload={r: v["attested"] for r, v in status["roles"].items()})
        return status

    # ── The routing decision ───────────────────────────────────────────────
    def infer(
        self,
        cur: psycopg.Cursor,
        task_type: str,
        prompt: str,
        *,
        run_id: str,
        node_name: str,
        attempt: int = 1,
        sensitivity: str = "open",
        system: str | None = None,
        max_tokens: int = 768,
    ) -> Completion:
        if task_type not in TASK_ROLE:
            raise ErosException(f"unknown task_type {task_type!r}")
        role = TASK_ROLE[task_type]
        spec = self.manifest.spec(role)

        # 1) SENSITIVITY — hard constraint, evaluated first (ADR-004).
        allow_external = (
            self.cfg.external_enabled
            and sensitivity == "open"
            and (self.cfg.anthropic_zdr_confirmed or self.cfg.openai_zdr_confirmed)
        )
        if sensitivity == "sensitive" and self.cfg.external_enabled:
            # External rungs are simply never built for sensitive content.
            allow_external = False

        # 2) LINEAGE + CONTEXT CEILING — never silent truncation.
        est_ctx = _estimate_tokens((system or "") + prompt) + max_tokens
        if est_ctx > spec.num_ctx:
            raise ContextCeilingExceeded(
                f"{role}: estimated {est_ctx} tokens exceeds derived ceiling {spec.num_ctx}",
                role=role, model=spec.modelfile_tag,
            )

        errors: list[str] = []

        # 3) SLOT + 4) local generation (primary rung).
        if self.ledger.healthy():
            try:
                self.ledger.ensure_loaded(cur, role, run_id=run_id)
                return self._ollama_generate(cur, run_id, spec.modelfile_tag, prompt, system, max_tokens)
            except (httpx.HTTPError, ErosException) as e:
                errors.append(f"ollama: {e}")
                logger.warning("ollama rung failed for %s: %s", role, e)
        else:
            errors.append("ollama: unhealthy")

        # llama.cpp CPU rung (if operated).
        if self.cfg.llamacpp_server_url:
            try:
                return self._llamacpp_generate(cur, run_id, spec.modelfile_tag, prompt, system, max_tokens)
            except httpx.HTTPError as e:
                errors.append(f"llamacpp: {e}")

        # External rungs — ZDR + budget + idempotency-before-dispatch.
        if allow_external:
            for provider, zdr in (("anthropic", self.cfg.anthropic_zdr_confirmed),
                                  ("openai", self.cfg.openai_zdr_confirmed)):
                if not zdr:
                    continue
                api_key = os.environ.get(f"{provider.upper()}_API_KEY")
                if not api_key:
                    errors.append(f"{provider}: no API key")
                    continue
                try:
                    return self._external_generate(
                        cur, run_id=run_id, node_name=node_name, attempt=attempt,
                        provider=provider, api_key=api_key,
                        prompt=prompt, system=system, max_tokens=max_tokens,
                    )
                except ErosException as e:
                    errors.append(f"{provider}: {e}")

        if sensitivity != "open" and not errors:
            raise SensitivityError("no local backend and external routing forbidden by sensitivity")
        raise ErosException("no inference backend available: " + " | ".join(errors),
                            task_type=task_type, role=role)

    # ── Backends ───────────────────────────────────────────────────────────
    def _ollama_generate(self, cur, run_id, tag, prompt, system, max_tokens) -> Completion:
        t0 = time.monotonic()
        body = {"model": tag, "prompt": prompt, "stream": False,
                "options": {"num_predict": max_tokens}}
        if system:
            body["system"] = system
        r = self.ledger.client.post("/api/generate", json=body)
        r.raise_for_status()
        data = r.json()
        latency = int((time.monotonic() - t0) * 1000)
        comp = Completion(
            text=data.get("response", ""), model=tag, provider="ollama",
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
            latency_ms=latency,
        )
        events.emit(cur, "model.infer", run_id=run_id,
                    payload={"provider": "ollama"}, latency_ms=latency,
                    token_count=comp.tokens_in + comp.tokens_out, model_name=tag)
        return comp

    def _llamacpp_generate(self, cur, run_id, tag, prompt, system, max_tokens) -> Completion:
        t0 = time.monotonic()
        full = (f"{system}\n\n{prompt}" if system else prompt)
        r = httpx.post(f"{self.cfg.llamacpp_server_url}/completion",
                       json={"prompt": full, "n_predict": max_tokens},
                       timeout=httpx.Timeout(10.0, read=600.0))
        r.raise_for_status()
        data = r.json()
        latency = int((time.monotonic() - t0) * 1000)
        comp = Completion(text=data.get("content", ""), model=tag, provider="llamacpp",
                          tokens_in=int(data.get("tokens_evaluated") or 0),
                          tokens_out=int(data.get("tokens_predicted") or 0),
                          latency_ms=latency)
        events.emit(cur, "model.infer", run_id=run_id, payload={"provider": "llamacpp"},
                    latency_ms=latency, token_count=comp.tokens_in + comp.tokens_out,
                    model_name=tag + " (cpu)")
        return comp

    def _external_generate(self, cur, *, run_id, node_name, attempt, provider,
                           api_key, prompt, system, max_tokens) -> Completion:
        pin, pout = EXTERNAL_PRICING[provider]
        est_cost = (_estimate_tokens(prompt) * pin + max_tokens * pout) / 1_000_000
        idem = idempotency_key(run_id, node_name, attempt, prompt_digest(prompt))

        res = self.governor.reserve(cur, run_id=run_id, node_name=node_name,
                                    idem_key=idem, estimated_cost=round(est_cost, 2) or 0.01,
                                    provider=provider)
        if res.status == "settled":
            # Resume path: never re-issue; re-read the response from events (§6.9).
            cur.execute(
                """SELECT payload FROM events
                   WHERE run_id = %s AND event_type = 'model.infer.external'
                     AND payload->>'idempotency_key' = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (run_id, idem),
            )
            row = cur.fetchone()
            if row:
                p = row["payload"]
                return Completion(p["text"], p["model"], provider,
                                  p["tokens_in"], p["tokens_out"], 0)
            raise ErosException("settled reservation has no recorded response; refusing to re-issue")
        if res.status == "orphaned":
            raise ErosException("prior dispatch orphaned (possibly billed); operator review required")

        request_id = f"eros-{uuid.uuid4()}"
        self.governor.record_dispatch(cur, res.id, request_id)  # BEFORE dispatch

        t0 = time.monotonic()
        text, tin, tout, model = self._call_external(provider, api_key, prompt, system,
                                                     max_tokens, request_id)
        latency = int((time.monotonic() - t0) * 1000)
        actual = (tin * pin + tout * pout) / 1_000_000
        self.governor.settle(cur, res.id, round(actual, 6))
        events.emit(cur, "model.infer.external", run_id=run_id,
                    payload={"provider": provider, "idempotency_key": idem,
                             "text": text, "model": model,
                             "tokens_in": tin, "tokens_out": tout},
                    latency_ms=latency, token_count=tin + tout,
                    model_name=model, cost_estimate=actual)
        return Completion(text, model, provider, tin, tout, latency)

    @staticmethod
    def _call_external(provider, api_key, prompt, system, max_tokens, request_id):
        if provider == "anthropic":
            body = {"model": os.environ.get("EROS_ANTHROPIC_MODEL", "claude-sonnet-4-5"),
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                    "metadata": {"user_id": request_id}}
            if system:
                body["system"] = system
            r = httpx.post("https://api.anthropic.com/v1/messages",
                           headers={"x-api-key": api_key,
                                    "anthropic-version": "2023-06-01"},
                           json=body, timeout=120.0)
            r.raise_for_status()
            d = r.json()
            text = "".join(b.get("text", "") for b in d.get("content", []))
            u = d.get("usage", {})
            return text, u.get("input_tokens", 0), u.get("output_tokens", 0), d.get("model", "anthropic")
        # openai
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        r = httpx.post("https://api.openai.com/v1/chat/completions",
                       headers={"Authorization": f"Bearer {api_key}"},
                       json={"model": os.environ.get("EROS_OPENAI_MODEL", "gpt-4o-mini"),
                             "max_tokens": max_tokens, "messages": msgs,
                             "user": request_id},
                       timeout=120.0)
        r.raise_for_status()
        d = r.json()
        u = d.get("usage", {})
        return (d["choices"][0]["message"]["content"],
                u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                d.get("model", "openai"))
