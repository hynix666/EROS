"""Universal Interface Layer (LIL) — canonical §6.1 / ADR-021.

Single entry point: REST + WebSocket here, MCP in eros.mcp_server. All
agents, loops, and humans interact through this boundary; it enforces
evidence capture (every call emits events), budget metering (Governor),
and the FR18 degraded-mode contract (an explicit fast-abort choice,
never a silent 3-hour CPU fallback).

Concurrency (C10): one active research run. A POST /research while a run
is active returns 202 RunQueued; the queue is durable in ``run_queue``
and drained FOR UPDATE SKIP LOCKED when the active run reaches a terminal
or paused state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from eros.config import get_static
from eros.db.pool import transaction
from eros.dgk.kernel import check_claim
from eros.errors import BudgetReservationFailed, ErosException
from eros.gate.heuristic import classify
from eros.governor.budget import Governor, idempotency_key, prompt_digest
from eros.lil import events
from eros.pipeline.graph import build_deps, create_run, detect_model_mode, run_graph
from eros.recovery.startup import reconcile

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("planning", "searching", "ingesting", "analyzing",
                   "verifying", "reporting", "evaluating")

app = FastAPI(title="EROS LIL", version="3.2.0")
_worker_lock = threading.Lock()


# ── lifecycle ───────────────────────────────────────────────────────────────
import contextlib


@contextlib.asynccontextmanager
async def _lifespan(app_: FastAPI):
    app_.state.reconcile = reconcile()         # startup is always recovery (§6.9)
    app_.state.deps = build_deps()
    app_.state.governor = Governor()
    logger.info("LIL up; model layer: %s", app_.state.reconcile.get("model_layer"))
    yield


app.router.lifespan_context = _lifespan


def _spawn_run(run_id: str, state) -> None:
    def _work():
        try:
            run_graph(run_id, state, deps=app.state.deps)
        finally:
            _drain_queue()
    threading.Thread(target=_work, name=f"run-{run_id}", daemon=True).start()


def _active_run(cur) -> dict | None:
    cur.execute("SELECT id, status FROM runs WHERE status = ANY(%s) LIMIT 1",
                (list(ACTIVE_STATUSES),))
    return cur.fetchone()


def _drain_queue() -> None:
    with _worker_lock:
        with transaction() as cur:
            if _active_run(cur):
                return
            cur.execute(
                """SELECT id, question, envelope, sensitivity FROM run_queue
                   ORDER BY priority DESC, created_at
                   FOR UPDATE SKIP LOCKED LIMIT 1""")
            row = cur.fetchone()
            if row is None:
                return
            cur.execute("DELETE FROM run_queue WHERE id = %s", (row["id"],))
        run_id, state = create_run(row["question"], sensitivity=row["sensitivity"],
                                   deps=app.state.deps)
        _spawn_run(run_id, state)


# ── research lifecycle ──────────────────────────────────────────────────────
class ResearchRequest(BaseModel):
    question: str = Field(min_length=3)
    sensitivity: str = Field(default="open", pattern="^(open|restricted|sensitive)$")
    degraded_ack: bool = False   # FR18: explicit consent to degraded mode


@app.post("/research", status_code=201)
def start_research(req: ResearchRequest):
    cfg = get_static()
    deps = app.state.deps

    # FR18 — never a silent default.
    model_mode, detail = detect_model_mode(deps)
    if cfg.require_local_generation and not model_mode and not req.degraded_ack:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "DegradedModeDetected",
                "reason": detail,
                "estimated_minutes": 180,
                "choices": ["Proceed in Degraded Mode (est. 180m): retry with degraded_ack=true",
                            "Abort"],
            },
        )

    with _worker_lock:
        with transaction() as cur:
            active = _active_run(cur)
            if active:  # C10 — one active run; durable queue
                decision = classify(req.question)
                cur.execute(
                    """INSERT INTO run_queue (question, envelope, sensitivity)
                       VALUES (%s, %s, %s) RETURNING id""",
                    (req.question, Json(decision.envelope), req.sensitivity),
                )
                qid = str(cur.fetchone()["id"])
                events.emit(cur, "run.queued", payload={"queue_id": qid,
                                                        "behind": str(active["id"])})
                return {"status": "RunQueued", "queue_id": qid,
                        "active_run": str(active["id"])}
        run_id, state = create_run(req.question, sensitivity=req.sensitivity, deps=deps)
        _spawn_run(run_id, state)
    return {"status": "started", "run_id": run_id, "model_mode": model_mode,
            "mode_detail": detail}


@app.get("/runs")
def list_runs(limit: int = 50):
    with transaction() as cur:
        cur.execute(
            """SELECT id, question, status, computed_sensitivity, escalated,
                      created_at, updated_at
               FROM runs ORDER BY created_at DESC LIMIT %s""", (limit,))
        return {"runs": [_jsonrow(r) for r in cur.fetchall()]}


@app.get("/research/{run_id}")
def get_run(run_id: str):
    with transaction() as cur:
        cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
        run = cur.fetchone()
        if run is None:
            raise HTTPException(404, "run not found")
        details = events.run_details(cur, run_id)
    return {"run": _jsonrow(run), "telemetry": [_jsonrow(d) for d in details]}


@app.delete("/research/{run_id}")
def cancel_run(run_id: str):
    with transaction() as cur:
        cur.execute("UPDATE runs SET cancel_requested = TRUE WHERE id = %s RETURNING id",
                    (run_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "run not found")
        events.emit(cur, "run.cancel_requested", run_id=run_id)
    return {"status": "cancel_requested", "note": "cooperative; honored at next node boundary"}


@app.post("/research/{run_id}/approve")
def approve_publish(run_id: str, decision: str = "approved"):
    """Human gate (publish). The database — not this endpoint — decides."""
    if decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved|rejected")
    with transaction() as cur:
        cur.execute(
            """UPDATE approvals SET decision = %s, decided_at = NOW(), actor = 'human'
               WHERE run_id = %s AND gate_name = 'publish' AND decision IS NULL
               RETURNING id""",
            (decision, run_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "no open publish gate for this run")
        if decision == "rejected":
            cur.execute("UPDATE runs SET status = 'cancelled' WHERE id = %s", (run_id,))
            events.emit(cur, "human_gate.rejected", run_id=run_id)
            return {"status": "rejected"}
    try:
        with transaction() as cur:
            cur.execute("UPDATE runs SET status = 'published' WHERE id = %s", (run_id,))
            events.emit(cur, "report.published", run_id=run_id,
                        payload={"via": "human_gate"})
        _drain_queue()
        return {"status": "published"}
    except Exception as e:
        with transaction() as cur:
            events.emit(cur, "gate.violation", run_id=run_id,
                        payload={"error": str(e).splitlines()[0]})
        raise HTTPException(409, f"trust chain refused publication: {e}")


@app.get("/research/{run_id}/report", response_class=PlainTextResponse)
def get_report(run_id: str):
    """Assemble Markdown from the Gate-3 ledger — the single source of truth."""
    with transaction() as cur:
        cur.execute(
            """SELECT s.ordinal, s.kind, s.text, s.claim_id, s.template_id,
                      c.confidence, c.verification_kind, ch.locator, a.url, a.hash
               FROM report_sentences s
               LEFT JOIN claims c ON c.id = s.claim_id
               LEFT JOIN chunks ch ON ch.id = c.primary_evidence_chunk_id
               LEFT JOIN artifacts a ON a.id = ch.artifact_id
               WHERE s.run_id = %s ORDER BY s.ordinal""",
            (run_id,),
        )
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, "no report ledger for this run (not yet reported)")
    lines, refs = [], []
    for r in rows:
        if r["kind"] == "structural" and r["template_id"] == "tpl.title":
            lines += [f"# {r['text']}", ""]
        elif r["kind"] == "assertive":
            refs.append(r)
            lines.append(f"- {r['text']} [^{len(refs)}]")
        elif r["kind"] == "disclosure":
            lines += ["", f"> **Disclosure.** {r['text']}"]
        else:
            lines += [r["text"], ""]
    if refs:
        lines += ["", "---", ""]
        for i, r in enumerate(refs, 1):
            src = r["url"] or f"artifact:{(r['hash'] or '')[:12]}"
            lines.append(f"[^{i}]: {src} · {r['locator']} · "
                         f"{r['verification_kind']} @ {r['confidence']}")
    return "\n".join(lines)


@app.get("/research/{run_id}/evidence")
def get_evidence(run_id: str):
    with transaction() as cur:
        cur.execute(
            """SELECT c.id, c.text, c.status, c.verification_kind, c.confidence,
                      c.computed_sensitivity, ch.locator, ch.text AS chunk_text,
                      a.url, a.source, a.hash
               FROM claims c
               JOIN chunks ch ON ch.id = c.primary_evidence_chunk_id
               JOIN artifacts a ON a.id = ch.artifact_id
               WHERE c.run_id = %s ORDER BY c.created_at""",
            (run_id,),
        )
        claims = [_jsonrow(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT g.claim_id, g.verdict, g.missing_numbers, g.missing_dates,
                      g.missing_entities, g.missing_quotations
               FROM groundedness_kernel_results g
               JOIN claims c ON c.id = g.claim_id WHERE c.run_id = %s""",
            (run_id,),
        )
        kernel = [_jsonrow(r) for r in cur.fetchall()]
    return {"claims": claims, "kernel_results": kernel}


# ── LIL contract endpoints (ADR-021 table) ──────────────────────────────────
class InferRequest(BaseModel):
    task_type: str
    prompt: str
    run_id: str
    sensitivity: str = "open"
    max_tokens: int = 512


@app.post("/lil/model/infer")
def lil_infer(req: InferRequest):
    deps = app.state.deps
    if deps.router is None:
        raise HTTPException(503, "model layer unavailable (no valid manifest)")
    try:
        with transaction() as cur:
            comp = deps.router.infer(cur, req.task_type, req.prompt,
                                     run_id=req.run_id, node_name="lil",
                                     sensitivity=req.sensitivity,
                                     max_tokens=req.max_tokens)
        return {"completion": comp.text, "model": comp.model, "provider": comp.provider,
                "tokens": comp.tokens_in + comp.tokens_out, "latency": comp.latency_ms}
    except ErosException as e:
        raise HTTPException(422, str(e))


class ReserveRequest(BaseModel):
    run_id: str
    node: str
    estimate: float


@app.post("/lil/budget/reserve")
def lil_reserve(req: ReserveRequest):
    idem = idempotency_key(req.run_id, req.node, 1, prompt_digest(f"lil:{req.node}"))
    try:
        with transaction() as cur:
            res = app.state.governor.reserve(cur, run_id=req.run_id, node_name=req.node,
                                             idem_key=idem, estimated_cost=req.estimate,
                                             provider="lil")
        return {"reservation_id": res.id, "status": res.status}
    except BudgetReservationFailed as e:
        raise HTTPException(402, str(e))


class ReleaseRequest(BaseModel):
    reservation_id: str
    actual: float = 0.0


@app.post("/lil/budget/release")
def lil_release(req: ReleaseRequest):
    with transaction() as cur:
        if req.actual > 0:
            app.state.governor.settle(cur, req.reservation_id, req.actual)
        else:
            app.state.governor.release(cur, req.reservation_id)
    return {"refund": "released" if req.actual == 0 else "settled"}


class VerifyRequest(BaseModel):
    claim_text: str
    evidence_texts: list[str]


@app.post("/lil/agent/verify")
def lil_verify(req: VerifyRequest):
    r = check_claim(req.claim_text, req.evidence_texts)
    return {"verdict": r.verdict,
            "missing": {"numbers": r.missing_numbers, "dates": r.missing_dates,
                        "entities": r.missing_entities, "quotations": r.missing_quotations},
            "tolerances": {"entity": r.entity_tolerance, "number": r.number_tolerance}}


@app.post("/lil/agent/spawn")
def lil_spawn(body: dict):
    """agent.spawn — Phase 1: one pipeline agent = one run."""
    prompt = str(body.get("prompt", "")).strip()
    if len(prompt) < 3:
        raise HTTPException(400, "prompt required")
    run_id, state = create_run(prompt, deps=app.state.deps)
    _spawn_run(run_id, state)
    return {"agent_id": run_id, "status": "started"}


@app.post("/lil/agent/delegate")
def lil_delegate(body: dict):
    raise HTTPException(
        501, "agent.delegate is a Phase 2 (orchestrator-worker) capability; "
             "Phase 1 runs the single Core Loop pipeline (C6)")


@app.get("/lil/memory/read")
def lil_memory_read(scope: str | None = None, query: str | None = None, limit: int = 50):
    """memory.read — Phase 1 episodic memory is the events table (§6.7)."""
    with transaction() as cur:
        rows = events.tail(cur, run_id=scope, limit=min(limit, 200))
    return {"events": [_jsonrow(r) for r in rows]}


@app.post("/lil/kvcache/hint")
def lil_kvcache_hint(body: dict):
    """kvcache.hint — Phase 1: Ollama owns KV natively (C5); the LIL observes."""
    role = body.get("model", "drafter")
    slot = {"drafter": "generation", "checker": "generation",
            "arbiter": "on_demand", "judge": "on_demand"}.get(role, "generation")
    return {"slot_assignment": slot,
            "note": "Phase 1: native Ollama KV; tiered offloading is the Phase 2 "
                    "vLLM+LMCache flag (ADR-002)"}


# ── WebSocket event tail ────────────────────────────────────────────────────
@app.websocket("/ws/events")
async def ws_events(ws: WebSocket, run_id: str | None = None):
    await ws.accept()
    watermark: datetime | None = None
    try:
        while True:
            def _poll(wm):
                with transaction() as cur:
                    return events.tail(cur, run_id=run_id, after=wm, limit=100)
            rows = await asyncio.to_thread(_poll, watermark)
            for r in rows:
                watermark = r["created_at"]
                await ws.send_json(_jsonrow(r))
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


# ── Knowledge-graph assist endpoints (frontend contract) ────────────────────
_TYPE_LABELS = {
    ("agent", "data"): "reads", ("agent", "action"): "performs",
    ("system", "component"): "contains", ("component", "component"): "interacts with",
    ("research", "data"): "produces", ("code", "system"): "implements",
    ("logic", "action"): "governs", ("project", "research"): "scopes",
}


def _kg_model_json(prompt: str) -> dict | None:
    deps = app.state.deps
    if deps.router is None:
        return None
    try:
        with transaction() as cur:
            # sensitivity='sensitive' structurally forbids external routing
            # for these ad-hoc, run-less calls (no budget FK involved).
            comp = deps.router.infer(cur, "judge", prompt, run_id=None,  # type: ignore[arg-type]
                                     node_name="kg", sensitivity="sensitive",
                                     max_tokens=200)
        start = comp.text.find("{")
        end = comp.text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(comp.text[start:end + 1])
    except Exception as e:
        logger.info("kg model path unavailable (%s); deterministic fallback", e)
    return None


@app.post("/api/smart-link")
def smart_link(body: dict):
    s, t = body.get("sourceNode", {}), body.get("targetNode", {})
    out = _kg_model_json(
        "Propose a 1-3 word edge label and one-sentence justification for a "
        'knowledge-graph link. Respond ONLY JSON {"label": str, "justification": str}.\n'
        f"Source: {s.get('id')} ({s.get('type')}): {s.get('description','')[:200]}\n"
        f"Target: {t.get('id')} ({t.get('type')}): {t.get('description','')[:200]}")
    if out and out.get("label"):
        return {"label": str(out["label"])[:40],
                "justification": str(out.get("justification", ""))[:300]}
    label = _TYPE_LABELS.get((s.get("type", ""), t.get("type", "")), "relates to")
    return {"label": label,
            "justification": f"Deterministic type-pair inference: "
                             f"{s.get('type','node')} → {t.get('type','node')}."}


@app.post("/api/infer-relationship")
def infer_relationship(body: dict):
    s, t = body.get("sourceNode", {}), body.get("targetNode", {})
    out = _kg_model_json(
        'Respond ONLY JSON {"label": str} — a 1-3 word relationship label.\n'
        f"Source: {s.get('id')} — Target: {t.get('id')}")
    if out and out.get("label"):
        return {"label": str(out["label"])[:40]}
    return {"label": _TYPE_LABELS.get((s.get("type", ""), t.get("type", "")), "relates to")}


@app.post("/api/query-graph")
def query_graph(body: dict):
    query = str(body.get("query", "")).lower()
    nodes = body.get("graphData", {}).get("nodes", [])
    terms = [w for w in query.split() if len(w) > 2]
    hits = [n["id"] for n in nodes
            if any(w in (n.get("id", "") + " " + n.get("description", "")).lower()
                   for w in terms)]
    return {"nodeIds": hits[:25]}


@app.get("/health")
@app.get("/api/health")
def health():
    with transaction() as cur:
        cur.execute("SELECT 1 AS ok")
        db = cur.fetchone()["ok"] == 1
    model_mode, detail = detect_model_mode(app.state.deps)
    return {"status": "ok", "db": db, "model_mode": model_mode, "mode_detail": detail,
            "gate4_mode": get_static().gate4_mode}


def _jsonrow(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else
                str(v) if k in ("id", "run_id", "claim_id") and v is not None else v)
            for k, v in row.items()}


# Static frontend (built by `npm run build` → frontend/dist), mounted last.
_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")


def main() -> None:
    uvicorn.run("eros.lil.app:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
