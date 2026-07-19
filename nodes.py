"""Pipeline nodes — canonical §6.3 / §6.5, phase-batched (C9).

Design invariants every node honors:

* **Idempotent or compensating (FR13).** Re-execution from the last
  checkpoint never double-writes: artifacts are content-addressed, drafts
  are looked-up-before-insert, report sentences are regenerated whole.
* **Role-scoped protected writes (ADR-017).** The Analyst's inserts run
  under ``SET LOCAL ROLE eros_analyst``; the Verifier's under
  ``eros_verifier``; the Reporter's under ``eros_reporter``; the Ingestor's
  under ``eros_ingestor``. The privilege boundary is enforced at runtime,
  not just proven in tests.
* **Cooperative cancellation** at every node boundary (FR10): budget
  released, browsers would be killed (Phase 1 has no browser pool), status
  → cancelled through the g00 transition trigger.
* **Status transitions go through the database**, so g00–g30 always fire.
* **Degradations are recorded, bounded, and disclosed** — every entry in
  ``state.degradations`` is rendered verbatim into the report footer.
* **A run that finds nothing says so (FR9).** The Evidence Sufficiency
  Gate re-plans at most twice, then terminates ``insufficient_evidence``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field

import psycopg

from eros.config import StaticConfig
from eros.db.pool import cancel_requested, set_run_status, transaction
from eros.dgk.kernel import check_claim
from eros.errors import ArtifactError, ErosException, LlmOutputMalformed, NoEvidenceFound
from eros.gate.heuristic import GateDecision
from eros.governor.budget import Governor
from eros.ingest.artifact_store import store_artifact
from eros.ingest.connectors import Connector, Source
from eros.ingest.processing import Embedder, chunk_text, extract_text
from eros.lil import events
from eros.pipeline.state import RunState
from eros.retrieval import hybrid
from eros.router.router import ModelRouter

logger = logging.getLogger(__name__)

MAX_TASKS = 4
MAX_CLAIMS = 20
QA_SAMPLE = 5
QA_THRESHOLD = 0.9


@dataclass
class Deps:
    cfg: StaticConfig
    governor: Governor
    embedder: Embedder
    connectors: list[Connector] = field(default_factory=list)
    router: ModelRouter | None = None  # None → deterministic degraded mode


@contextlib.contextmanager
def _as_role(cur: psycopg.Cursor, role: str):
    """Protected writes under the pipeline role. On success, RESET ROLE so
    follow-up statements run as the app; on failure the transaction is dead
    and rolls back (taking SET LOCAL with it) — issuing RESET there would
    only mask the original error with 'transaction is aborted'."""
    cur.execute(psycopg.sql.SQL("SET LOCAL ROLE {}").format(psycopg.sql.Identifier(role)))
    yield
    cur.execute("RESET ROLE")


def _parse_json_block(text: str):
    """Tolerant JSON extraction: fenced block or first bracket span."""
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        raise LlmOutputMalformed("no JSON found in model output")
    for end in range(len(text), start, -1):
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
    raise LlmOutputMalformed("unparseable JSON in model output")


class PipelineNodes:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    # ── shared helpers ─────────────────────────────────────────────────────
    def _cancelled(self, state: RunState) -> bool:
        run_id = state["run_id"]
        with transaction() as cur:
            if not cancel_requested(cur, run_id):
                return False
            released = self.deps.governor.release_run(cur, run_id)
            set_run_status(cur, run_id, "cancelled")
            events.emit(cur, "run.cancelled", run_id=run_id,
                        payload={"budget_reservations_released": released})
        return True

    def _node_event(self, run_id: str, node: str, phase: str, **payload) -> None:
        with transaction() as cur:
            events.emit(cur, f"node.{phase}", run_id=run_id,
                        payload={"node": node, **payload})

    def _infer(self, cur, task, prompt, *, state: RunState, node: str,
               attempt: int = 1, system: str | None = None, max_tokens: int = 768):
        assert self.deps.router is not None
        return self.deps.router.infer(
            cur, task, prompt, run_id=state["run_id"], node_name=node,
            attempt=attempt, sensitivity=state.get("sensitivity", "open"),
            system=system, max_tokens=max_tokens,
        )

    # ── 1. Planner ─────────────────────────────────────────────────────────
    def plan(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id, question = state["run_id"], state["question"]
        t0 = time.monotonic()
        self._node_event(run_id, "plan", "start")

        tasks: list[str] = []
        if state.get("model_mode") and self.deps.router:
            prompt = (
                "Decompose this research question into 2-4 focused web-search "
                "queries. Respond with ONLY a JSON array of strings.\n\n"
                f"Question: {question}"
            )
            for attempt in (1, 2, 3):  # structural-correction retry ×2 (§6.11)
                try:
                    with transaction() as cur:
                        comp = self._infer(cur, "draft", prompt, state=state,
                                           node="plan", attempt=attempt, max_tokens=256)
                    parsed = _parse_json_block(comp.text)
                    tasks = [str(t).strip() for t in parsed if str(t).strip()][:MAX_TASKS]
                    if tasks:
                        break
                except (LlmOutputMalformed, ErosException) as e:
                    logger.warning("plan attempt %d failed: %s", attempt, e)
                    prompt += "\n\nYour previous output was not a valid JSON array. Output ONLY the JSON array."
        if not tasks:
            # Deterministic decomposition — honest, bounded, always works.
            parts = re.split(r"\band\b|;", question)
            tasks = [p.strip(" ?.") for p in parts if len(p.split()) >= 3][:MAX_TASKS] or [question]

        with transaction() as cur:
            set_run_status(cur, run_id, "searching")
            events.emit(cur, "plan.created", run_id=run_id, payload={"tasks": tasks},
                        latency_ms=int((time.monotonic() - t0) * 1000))
        return {"plan": tasks, "route": "ok"}

    # ── 2. Searcher ────────────────────────────────────────────────────────
    def search(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        self._node_event(run_id, "search", "start", tasks=len(state.get("plan", [])))
        max_sources = int(state.get("envelope", {}).get("max_sources",
                                                        self.deps.cfg.max_sources_per_run))
        seen = {s["url"] for s in state.get("sources", [])}
        sources: list[dict] = list(state.get("sources", []))

        per_task = max(1, max_sources // max(1, len(state.get("plan", [1]))))
        for task in state.get("plan", []):
            for conn in self.deps.connectors:
                if len(sources) >= max_sources:
                    break
                try:
                    hits: list[Source] = conn.search(task, per_task)
                except Exception as e:  # a failing engine never fails the run
                    logger.warning("connector %s failed for %r: %s", conn.name, task, e)
                    continue
                for h in hits:
                    if h.url in seen or len(sources) >= max_sources:
                        continue
                    seen.add(h.url)
                    sources.append({"url": h.url, "title": h.title, "connector": h.connector})
                    with transaction() as cur:
                        events.emit(cur, "SourceDiscovered", run_id=run_id,
                                    payload={"url": h.url, "title": h.title,
                                             "connector": h.connector})
        with transaction() as cur:
            set_run_status(cur, run_id, "ingesting")
        return {"sources": sources, "route": "ok"}

    # ── 3. Ingestor (role: eros_ingestor) ──────────────────────────────────
    def ingest(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        self._node_event(run_id, "ingest", "start", sources=len(state.get("sources", [])))
        by_name = {c.name: c for c in self.deps.connectors}
        artifact_ids: list[str] = list(state.get("artifact_ids", []))

        for src in state.get("sources", []):
            conn = by_name.get(src.get("connector")) or (self.deps.connectors[0]
                                                         if self.deps.connectors else None)
            if conn is None:
                break
            try:
                fetched = conn.fetch(src["url"])
                text = extract_text(fetched.content, fetched.content_type)
                if len(text) < 200:
                    raise ArtifactError("extracted text too short to be evidence",
                                        url=src["url"], length=len(text))
                with transaction() as cur:
                    with _as_role(cur, "eros_ingestor"):
                        artifact_id, created = store_artifact(
                            cur, content=fetched.content, source=fetched.source,
                            url=fetched.url, run_id=run_id,
                        )
                        if created or not self._has_chunks(cur, artifact_id):
                            self._write_chunks(cur, artifact_id, text)
                if artifact_id not in artifact_ids:
                    artifact_ids.append(artifact_id)
            except ArtifactError as e:
                # Taxonomy: quarantine, audit, continue run, log gap.
                with transaction() as cur:
                    events.emit(cur, "artifact.quarantined", run_id=run_id,
                                payload={"url": src.get("url"), "reason": str(e)})
            except Exception as e:
                with transaction() as cur:
                    events.emit(cur, "artifact.fetch_failed", run_id=run_id,
                                payload={"url": src.get("url"), "reason": str(e)})

        with transaction() as cur:
            set_run_status(cur, run_id, "analyzing")
        return {"artifact_ids": artifact_ids, "route": "ok"}

    @staticmethod
    def _has_chunks(cur, artifact_id: str) -> bool:
        cur.execute("SELECT 1 FROM chunks WHERE artifact_id = %s LIMIT 1", (artifact_id,))
        return cur.fetchone() is not None

    def _write_chunks(self, cur, artifact_id: str, text: str) -> None:
        pieces = chunk_text(text)
        if not pieces:
            return
        vectors = self.deps.embedder.embed(pieces)
        for i, (piece, vec) in enumerate(zip(pieces, vectors)):
            cur.execute(
                """INSERT INTO chunks (artifact_id, locator, text, embedding, fts)
                   VALUES (%s, %s, %s, %s::vector, to_tsvector('english', %s))""",
                (artifact_id, f"chunk-{i}", piece,
                 str(vec) if vec is not None else None, piece),
            )

    # ── 4. Retriever + Evidence Sufficiency Gate ───────────────────────────
    def retrieve(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        self._node_event(run_id, "retrieve", "start")
        try:
            with transaction() as cur:
                chunks = hybrid.retrieve(cur, state["question"],
                                         embedder=self.deps.embedder, k=12)
                hybrid.assert_sufficient(chunks)
                sens = hybrid.computed_sensitivity(chunks)
                cur.execute("UPDATE runs SET computed_sensitivity = %s WHERE id = %s",
                            (sens, run_id))
                events.emit(cur, "evidence.sufficient", run_id=run_id,
                            payload={"chunks": len(chunks), "sensitivity": sens})
            return {"chunk_ids": [c.chunk_id for c in chunks],
                    "sensitivity": sens, "route": "ok"}
        except NoEvidenceFound as e:
            replans = int(state.get("replans_used", 0))
            if replans < self.deps.cfg.max_replans:
                broadened = [f"{t} overview" for t in state.get("plan", [])] or [state["question"]]
                with transaction() as cur:
                    set_run_status(cur, run_id, "searching")   # analyzing → searching (legal)
                    events.emit(cur, "evidence.replan", run_id=run_id,
                                payload={"replans_used": replans + 1, "reason": str(e)})
                return {"replans_used": replans + 1, "plan": broadened,
                        "sources": [], "route": "replan"}
            with transaction() as cur:
                set_run_status(cur, run_id, "insufficient_evidence")
                events.emit(cur, "run.insufficient_evidence", run_id=run_id,
                            payload={"reason": str(e)})
            return {"route": "insufficient"}

    # ── 5. Analyst (role: eros_analyst — draft_claim_evidence ONLY) ───────
    def analyze(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        self._node_event(run_id, "analyze", "start", chunks=len(state.get("chunk_ids", [])))
        chunk_rows = self._load_chunks(state.get("chunk_ids", []))
        drafts = (self._model_drafts(state, chunk_rows)
                  if state.get("model_mode") and self.deps.router else None)
        degradations = list(state.get("degradations", []))
        if drafts is None:
            drafts = self._extractive_drafts(chunk_rows)
            note = "analyst degraded to deterministic extractive drafting (no model output)"
            if note not in degradations:
                degradations.append(note)

        analyst_model = (self.deps.router.manifest.spec("drafter").modelfile_tag
                         if state.get("model_mode") and self.deps.router else "extractive")
        with transaction() as cur:
            with _as_role(cur, "eros_analyst"):
                for d in drafts[:MAX_CLAIMS]:
                    cur.execute(
                        """SELECT 1 FROM draft_claim_evidence
                           WHERE run_id = %s AND claim_text = %s
                             AND primary_evidence_chunk_id = %s""",
                        (run_id, d["claim"], d["chunk_id"]),
                    )
                    if cur.fetchone():  # idempotent resume (FR13)
                        continue
                    cur.execute(
                        """INSERT INTO draft_claim_evidence
                               (run_id, claim_text, primary_evidence_chunk_id,
                                confidence, analyst_model)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (run_id, d["claim"], d["chunk_id"], d["confidence"], analyst_model),
                    )
            events.emit(cur, "claims.drafted", run_id=run_id,
                        payload={"count": min(len(drafts), MAX_CLAIMS)})
            set_run_status(cur, run_id, "verifying")
        return {"degradations": degradations, "route": "ok"}

    def _load_chunks(self, chunk_ids: list[str]) -> list[dict]:
        if not chunk_ids:
            return []
        with transaction() as cur:
            cur.execute("SELECT id, text, locator FROM chunks WHERE id = ANY(%s)",
                        (chunk_ids,))
            rows = {str(r["id"]): r for r in cur.fetchall()}
        return [rows[c] for c in chunk_ids if c in rows]

    def _model_drafts(self, state: RunState, chunk_rows: list[dict]) -> list[dict] | None:
        numbered = "\n\n".join(f"[{i}] {r['text'][:1200]}" for i, r in enumerate(chunk_rows))
        prompt = (
            "From ONLY the evidence excerpts below, draft factual claims that answer "
            f"the question. Every claim must be directly supported by exactly one "
            "excerpt. Respond with ONLY a JSON array of objects: "
            '{"claim": str, "chunk": int, "confidence": float}.\n\n'
            f"Question: {state['question']}\n\nEvidence:\n{numbered}"
        )
        for attempt in (1, 2, 3):
            try:
                with transaction() as cur:
                    comp = self._infer(cur, "draft", prompt, state=state,
                                       node="analyze", attempt=attempt, max_tokens=1024)
                parsed = _parse_json_block(comp.text)
                out = []
                for item in parsed:
                    idx = int(item["chunk"])
                    if 0 <= idx < len(chunk_rows) and str(item.get("claim", "")).strip():
                        out.append({
                            "claim": str(item["claim"]).strip(),
                            "chunk_id": str(chunk_rows[idx]["id"]),
                            "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.6)))),
                        })
                if out:
                    return out
            except (LlmOutputMalformed, ErosException, KeyError, ValueError, TypeError) as e:
                logger.warning("analyze attempt %d failed: %s", attempt, e)
                prompt += "\n\nOutput ONLY the JSON array, nothing else."
        return None

    @staticmethod
    def _extractive_drafts(chunk_rows: list[dict]) -> list[dict]:
        """Deterministic degraded Analyst: the most information-dense sentence
        of each chunk, verbatim — evidence-faithful by construction."""
        drafts = []
        for r in chunk_rows[:10]:
            sentences = re.split(r"(?<=[.!?])\s+", r["text"])
            scored = sorted(
                (s for s in sentences if 40 <= len(s) <= 400),
                key=lambda s: (bool(re.search(r"\d", s)), len(s)), reverse=True,
            )
            if scored:
                drafts.append({"claim": scored[0].strip(), "chunk_id": str(r["id"]),
                               "confidence": 0.55})
        return drafts

    # ── 6. Verifier (role: eros_verifier — sole writer of claim_evidence) ──
    def verify(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        self._node_event(run_id, "verify", "start")
        degradations = list(state.get("degradations", []))
        checker_tag = None
        if state.get("model_mode") and self.deps.router:
            # Resume re-attestation before any verification write (§6.9);
            # g05 reads exactly what this records.
            with transaction() as cur:
                self.deps.router.record_lineage(cur, run_id)
            checker_tag = self.deps.router.manifest.spec("checker").modelfile_tag
        else:
            note = ("verification degraded to deterministic-kernel-only "
                    "(no cross-family model check this run)")
            if note not in degradations:
                degradations.append(note)

        with transaction() as cur:
            cur.execute(
                """SELECT id, claim_text, primary_evidence_chunk_id, confidence
                   FROM draft_claim_evidence
                   WHERE run_id = %s AND status = 'pending' ORDER BY created_at""",
                (run_id,),
            )
            pending = cur.fetchall()

        verified = rejected = contested = 0
        for d in pending:
            chunk_texts = self._evidence_texts(str(d["primary_evidence_chunk_id"]))
            kres = check_claim(d["claim_text"], chunk_texts)

            with transaction() as cur:
                with _as_role(cur, "eros_verifier"):
                    cur.execute(
                        "UPDATE draft_claim_evidence SET status = 'promoted' WHERE id = %s",
                        (d["id"],),
                    )
                    cur.execute(
                        """SELECT id FROM claims
                           WHERE run_id = %s AND text = %s AND primary_evidence_chunk_id = %s
                           ORDER BY created_at DESC LIMIT 1""",
                        (run_id, d["claim_text"], d["primary_evidence_chunk_id"]),
                    )
                    claim_id = str(cur.fetchone()["id"])
                    cur.execute(
                        """INSERT INTO groundedness_kernel_results
                               (claim_id, verdict, missing_numbers, missing_dates,
                                missing_entities, missing_quotations,
                                entity_tolerance, number_tolerance)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (claim_id, kres.verdict, kres.missing_numbers, kres.missing_dates,
                         kres.missing_entities, kres.missing_quotations,
                         kres.entity_tolerance, kres.number_tolerance),
                    )
                    if kres.verdict == "UNGROUNDED":
                        # FR14: proven-ungrounded claims cannot survive to publication.
                        cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
                        cur.execute(
                            "UPDATE draft_claim_evidence SET status = 'rejected' WHERE id = %s",
                            (d["id"],),
                        )
                        rejected += 1
                        events.emit(cur, "claim.rejected", run_id=run_id,
                                    payload={"claim": d["claim_text"][:200],
                                             "missing": {
                                                 "numbers": kres.missing_numbers,
                                                 "dates": kres.missing_dates,
                                                 "entities": kres.missing_entities,
                                                 "quotations": kres.missing_quotations}})
                        continue

                    # Deterministic supports row — the kernel's anchor check.
                    cur.execute(
                        """INSERT INTO claim_evidence
                               (claim_id, chunk_id, relation, checked_by_model,
                                verification_kind, confidence)
                           VALUES (%s, %s, 'supports', 'dgk-kernel', 'deterministic', 0.60)""",
                        (claim_id, d["primary_evidence_chunk_id"]),
                    )
                    status, kind, conf = "verified", "deterministic", 0.60

                    if checker_tag:
                        rel, mconf = self._model_check(state, d["claim_text"], chunk_texts[0])
                        if rel is not None:
                            cur.execute(
                                """INSERT INTO claim_evidence
                                       (claim_id, chunk_id, relation, checked_by_model,
                                        verification_kind, confidence, contest_strength)
                                   VALUES (%s, %s, %s, %s, 'cross-family', %s, %s)""",
                                (claim_id, d["primary_evidence_chunk_id"], rel,
                                 checker_tag, mconf,
                                 (round(abs(mconf - float(d["confidence"])), 2)
                                  if rel == "contradicts" else None)),
                            )
                            kind = "cross-family"
                            if rel == "supports":
                                status, conf = "verified", mconf
                            else:
                                status, conf = "contested", mconf
                                contested += 1
                    cur.execute(
                        """UPDATE claims SET status = %s, verification_kind = %s,
                                             confidence = %s
                           WHERE id = %s""",
                        (status, kind, conf, claim_id),
                    )
                    if status == "verified":
                        verified += 1

        with transaction() as cur:
            events.emit(cur, "verification.complete", run_id=run_id,
                        payload={"verified": verified, "rejected_ungrounded": rejected,
                                 "contested": contested})
        return {"degradations": degradations, "route": "ok"}

    def _evidence_texts(self, chunk_id: str) -> list[str]:
        with transaction() as cur:
            cur.execute("SELECT text FROM chunks WHERE id = %s", (chunk_id,))
            row = cur.fetchone()
        return [row["text"]] if row else [""]

    def _model_check(self, state: RunState, claim: str, evidence: str):
        prompt = (
            "Does the evidence support or contradict the claim? Respond with ONLY "
            'a JSON object {"relation": "supports"|"contradicts", "confidence": float}.\n\n'
            f"Claim: {claim}\n\nEvidence: {evidence[:2000]}"
        )
        for attempt in (1, 2):
            try:
                with transaction() as cur:
                    comp = self._infer(cur, "verify", prompt, state=state,
                                       node="verify", attempt=attempt, max_tokens=128)
                parsed = _parse_json_block(comp.text)
                rel = parsed.get("relation")
                if rel in ("supports", "contradicts"):
                    return rel, max(0.0, min(1.0, float(parsed.get("confidence", 0.7))))
            except (LlmOutputMalformed, ErosException, ValueError, TypeError) as e:
                logger.warning("model check attempt %d failed: %s", attempt, e)
        return None, 0.0  # checker unavailable → deterministic kind stands

    # ── 7. Arbiter (contested claims; on-demand slot) ──────────────────────
    def arbitrate(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        with transaction() as cur:
            cur.execute(
                "SELECT id, text, confidence FROM claims WHERE run_id = %s AND status = 'contested'",
                (run_id,),
            )
            contested = cur.fetchall()
        degradations = list(state.get("degradations", []))

        if contested and not (state.get("model_mode") and self.deps.router):
            # Canonical degraded mode 'no_arbiter': declared, bounded, disclosed.
            with transaction() as cur:
                cur.execute(
                    """INSERT INTO degraded_mode_log
                           (run_id, mode, exit_criterion, max_duration, capability_loss)
                       VALUES (%s, 'no_arbiter',
                               'arbiter model resident and attested at next run',
                               INTERVAL '24:00:00',
                               'contested claims surfaced without adjudication')""",
                    (run_id,),
                )
            note = "no_arbiter: contested claims surfaced without adjudication"
            if note not in degradations:
                degradations.append(note)
        elif contested:
            for c in contested:
                self._adjudicate_one(state, c)

        with transaction() as cur:
            set_run_status(cur, run_id, "reporting")
        return {"degradations": degradations, "route": "ok"}

    def _adjudicate_one(self, state: RunState, claim_row: dict) -> None:
        run_id, claim_id = state["run_id"], str(claim_row["id"])
        with transaction() as cur:
            cur.execute(
                """SELECT ce.relation, ce.confidence, ch.text
                   FROM claim_evidence ce JOIN chunks ch ON ch.id = ce.chunk_id
                   WHERE ce.claim_id = %s""",
                (claim_id,),
            )
            chains = cur.fetchall()
        summary = "\n".join(f"- [{c['relation']} @ {c['confidence']}] {c['text'][:500]}"
                            for c in chains)
        prompt = (
            "Adjudicate the contested claim against both evidence chains. Respond "
            'ONLY with JSON {"verdict": "uphold"|"reject"|"contested"}.\n\n'
            f"Claim: {claim_row['text']}\n\nEvidence chains:\n{summary}"
        )
        verdict = "contested"
        try:
            with transaction() as cur:
                comp = self._infer(cur, "adjudicate", prompt, state=state,
                                   node="arbitrate", max_tokens=64)
            v = _parse_json_block(comp.text).get("verdict")
            if v in ("uphold", "reject", "contested"):
                verdict = v
        except (LlmOutputMalformed, ErosException) as e:
            logger.warning("arbitration failed for %s: %s", claim_id, e)
        with transaction() as cur:
            with _as_role(cur, "eros_verifier"):
                if verdict == "uphold":
                    cur.execute("UPDATE claims SET status = 'verified' WHERE id = %s", (claim_id,))
                elif verdict == "reject":
                    cur.execute("DELETE FROM claims WHERE id = %s", (claim_id,))
                    cur.execute(
                        """UPDATE draft_claim_evidence SET status = 'rejected'
                           WHERE run_id = %s AND claim_text = %s""",
                        (run_id, claim_row["text"]),
                    )
            events.emit(cur, "claim.adjudicated", run_id=run_id,
                        payload={"claim_id": claim_id, "verdict": verdict})

    # ── 8. Reporter (role: eros_reporter — writes the Gate-3 ledger) ───────
    def report(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        self._node_event(run_id, "report", "start")
        with transaction() as cur:
            cur.execute(
                """SELECT id, text, status FROM claims
                   WHERE run_id = %s AND status IN ('verified','contested')
                   ORDER BY created_at""",
                (run_id,),
            )
            claims = cur.fetchall()

            with _as_role(cur, "eros_reporter"):
                cur.execute("DELETE FROM report_sentences WHERE run_id = %s", (run_id,))
                ordinal = 0

                def emit_sentence(text, kind, claim_id=None, template_id=None):
                    nonlocal ordinal
                    ordinal += 1
                    cur.execute(
                        """INSERT INTO report_sentences
                               (run_id, ordinal, text, kind, claim_id, template_id)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (run_id, ordinal, text, kind, claim_id, template_id),
                    )

                emit_sentence(f"Research report: {state['question']}",
                              "structural", template_id="tpl.title")
                emit_sentence(
                    "Method: autonomous evidence pipeline; every assertive sentence "
                    "below names a verified or contested claim backed by stored evidence.",
                    "structural", template_id="tpl.method")
                for c in claims:
                    if c["status"] == "contested":
                        emit_sentence("The following claim is contested by conflicting "
                                      "evidence and is presented as explicit uncertainty:",
                                      "disclosure", template_id="tpl.contested")
                    emit_sentence(c["text"], "assertive", claim_id=str(c["id"]))
                for note in state.get("degradations", []):
                    emit_sentence(f"Degraded mode disclosure: {note}",
                                  "disclosure", template_id="tpl.degradation")
                emit_sentence(
                    "Provenance: every claim above resolves to stored evidence chunks; "
                    "see the evidence browser for chunk-level locators and hashes.",
                    "disclosure", template_id="tpl.footer_provenance")

            events.emit(cur, "report.assembled", run_id=run_id,
                        payload={"sentences": ordinal, "claims": len(claims)})
            set_run_status(cur, run_id, "evaluating")
        return {"route": "ok"}

    # ── 9. QA-Eval (sampled groundedness ≥ 0.9; one bounded revision) ──────
    def qa_eval(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        with transaction() as cur:
            cur.execute(
                """SELECT s.id, s.text, s.claim_id, c.primary_evidence_chunk_id
                   FROM report_sentences s JOIN claims c ON c.id = s.claim_id
                   WHERE s.run_id = %s AND s.kind = 'assertive'
                   ORDER BY s.ordinal LIMIT %s""",
                (run_id, QA_SAMPLE),
            )
            sample = cur.fetchall()
        if not sample:
            return {"route": "ok"}

        scores, failing = [], []
        for s in sample:
            ev = self._evidence_texts(str(s["primary_evidence_chunk_id"]))
            score = None
            if state.get("model_mode") and self.deps.router:
                score = self._judge_score(state, s["text"], ev[0])
            if score is None:  # deterministic fallback: the kernel re-check
                score = 0.0 if check_claim(s["text"], ev).verdict == "UNGROUNDED" else 1.0
            scores.append(score)
            if score < QA_THRESHOLD:
                failing.append(str(s["claim_id"]))

        avg = sum(scores) / len(scores)
        with transaction() as cur:
            events.emit(cur, "qa.groundedness", run_id=run_id,
                        payload={"avg": round(avg, 3), "sampled": len(scores)})

        if avg >= QA_THRESHOLD:
            return {"route": "ok"}
        if int(state.get("revisions_used", 0)) < self.deps.cfg.max_qa_revisions:
            with transaction() as cur:
                with _as_role(cur, "eros_verifier"):
                    for cid in failing:
                        cur.execute("UPDATE claims SET status = 'stale' WHERE id = %s", (cid,))
                set_run_status(cur, run_id, "reporting")   # evaluating → reporting (legal)
                events.emit(cur, "qa.revision", run_id=run_id,
                            payload={"stale_claims": len(failing)})
            return {"revisions_used": int(state.get("revisions_used", 0)) + 1,
                    "route": "revise"}
        with transaction() as cur:
            cur.execute(
                """INSERT INTO approvals (run_id, gate_name, expires_at)
                   VALUES (%s, 'publish', NOW() + INTERVAL '24 hours')""",
                (run_id,),
            )
            set_run_status(cur, run_id, "paused_approval")
            events.emit(cur, "human_gate.opened", run_id=run_id,
                        payload={"gate": "publish", "reason": f"groundedness {avg:.2f} < 0.9"})
        return {"route": "paused"}

    def _judge_score(self, state: RunState, sentence: str, evidence: str) -> float | None:
        prompt = (
            "Score 0.0-1.0 how fully the evidence grounds the sentence. Respond "
            'ONLY with JSON {"score": float}.\n\n'
            f"Sentence: {sentence}\n\nEvidence: {evidence[:2000]}"
        )
        try:
            with transaction() as cur:
                comp = self._infer(cur, "judge", prompt, state=state,
                                   node="qa_eval", max_tokens=32)
            return max(0.0, min(1.0, float(_parse_json_block(comp.text)["score"])))
        except (LlmOutputMalformed, ErosException, KeyError, ValueError, TypeError):
            return None

    # ── 10. Finalize: the database has the last word ───────────────────────
    def finalize(self, state: RunState) -> dict:
        if self._cancelled(state):
            return {"route": "cancelled"}
        run_id = state["run_id"]
        try:
            with transaction() as cur:
                set_run_status(cur, run_id, "published")  # g10/g20/g30 fire here
                cur.execute(
                    """SELECT ordinal, kind, text, claim_id FROM report_sentences
                       WHERE run_id = %s ORDER BY ordinal""",
                    (run_id,),
                )
                ledger = cur.fetchall()
                digest = hashlib.sha256(
                    "\n".join(f"{r['ordinal']}|{r['kind']}|{r['text']}|{r['claim_id']}"
                              for r in ledger).encode()
                ).hexdigest()
                events.emit(cur, "report.published", run_id=run_id,
                            payload={"ledger_sha256": digest, "sentences": len(ledger)})
            return {"route": "ok"}
        except psycopg.errors.RaiseException as e:
            # A gate refused publication. Actionable error surfaced; run pauses.
            with transaction() as cur:
                events.emit(cur, "gate.violation", run_id=run_id,
                            payload={"error": str(e).splitlines()[0]})
                set_run_status(cur, run_id, "paused_approval")
            logger.error("publication refused by trust chain: %s", e)
            return {"route": "paused"}
