"""Workflow graph — canonical §6.3 / ADR-001 / C9.

Shape (phase-batched; the CI test asserts no path re-enters analyze):

    plan → search → ingest → retrieve ─┬─ ok ──────→ analyze → verify
                       ▲               ├─ replan ──→ search   (bounded ×2)
                       │               └─ insufficient → END (honest terminal)
                       │
    verify → arbitrate → report → qa_eval ─┬─ ok ────→ finalize → END
                              ▲            ├─ revise → report  (bounded ×1)
                              │            └─ paused/cancelled → END

Checkpointing: LangGraph's PostgresSaver against the same database
(ADR-001/ADR-003) — pause/resume across process restarts (FR7), thread_id
= run_id. Startup is always treated as a recovery event by the caller
(eros.recovery.startup) before any resume is attempted.
"""
from __future__ import annotations

import logging

from psycopg.types.json import Json

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, StateGraph

from eros.config import StaticConfig, get_static
from eros.db.pool import transaction
from eros.gate.heuristic import classify
from eros.governor.budget import Governor
from eros.ingest.connectors import load_connectors
from eros.ingest.processing import Embedder
from eros.lil import events
from eros.pipeline.nodes import Deps, PipelineNodes
from eros.pipeline.state import RunState, initial_state
from eros.router.attestation import attest_all, load_manifest
from eros.router.router import ModelRouter
from eros.router.slot_ledger import SlotLedger

logger = logging.getLogger(__name__)

_TERMINAL_ROUTES = {"insufficient", "paused", "cancelled"}


def build_graph(deps: Deps):
    """Compile-ready StateGraph. Kept side-effect-free for shape testing."""
    nodes = PipelineNodes(deps)
    g = StateGraph(RunState)

    g.add_node("plan", nodes.plan)
    g.add_node("search", nodes.search)
    g.add_node("ingest", nodes.ingest)
    g.add_node("retrieve", nodes.retrieve)
    g.add_node("analyze", nodes.analyze)
    g.add_node("verify", nodes.verify)
    g.add_node("arbitrate", nodes.arbitrate)
    g.add_node("report", nodes.report)
    g.add_node("qa_eval", nodes.qa_eval)
    g.add_node("finalize", nodes.finalize)

    g.set_entry_point("plan")

    def after(route_map):
        def _route(state: RunState) -> str:
            r = state.get("route", "ok")
            return route_map.get(r, route_map["ok"])
        return _route

    g.add_conditional_edges("plan", after({"ok": "search", "cancelled": END}),
                            {"search": "search", END: END})
    g.add_conditional_edges("search", after({"ok": "ingest", "cancelled": END}),
                            {"ingest": "ingest", END: END})
    g.add_conditional_edges("ingest", after({"ok": "retrieve", "cancelled": END}),
                            {"retrieve": "retrieve", END: END})
    g.add_conditional_edges(
        "retrieve",
        after({"ok": "analyze", "replan": "search",
               "insufficient": END, "cancelled": END}),
        {"analyze": "analyze", "search": "search", END: END},
    )
    g.add_conditional_edges("analyze", after({"ok": "verify", "cancelled": END}),
                            {"verify": "verify", END: END})
    g.add_conditional_edges("verify", after({"ok": "arbitrate", "cancelled": END}),
                            {"arbitrate": "arbitrate", END: END})
    g.add_conditional_edges("arbitrate", after({"ok": "report", "cancelled": END}),
                            {"report": "report", END: END})
    g.add_conditional_edges("report", after({"ok": "qa_eval", "cancelled": END}),
                            {"qa_eval": "qa_eval", END: END})
    g.add_conditional_edges(
        "qa_eval",
        after({"ok": "finalize", "revise": "report",
               "paused": END, "cancelled": END}),
        {"finalize": "finalize", "report": "report", END: END},
    )
    g.add_edge("finalize", END)
    return g


# ── Dependency construction ─────────────────────────────────────────────────
def build_deps(cfg: StaticConfig | None = None) -> Deps:
    cfg = cfg or get_static()
    governor = Governor()
    embedder = Embedder()
    connectors = load_connectors()
    router: ModelRouter | None = None
    try:
        manifest = load_manifest(cfg.resolved_manifest_path)
        ledger = SlotLedger(manifest, cfg.ollama_base_url)
        router = ModelRouter(cfg, manifest, ledger, governor)
    except Exception as e:  # manifest missing/invalid → deterministic mode, honestly
        logger.warning("model layer unavailable (%s); running deterministic mode", e)
    return Deps(cfg=cfg, governor=governor, embedder=embedder,
                connectors=connectors, router=router)


def detect_model_mode(deps: Deps) -> tuple[bool, str]:
    if deps.router is None:
        return False, "no valid model manifest"
    ok, why = deps.router.probe_local_generation()
    if ok:
        return True, "ollama"
    if deps.cfg.llamacpp_server_url:
        return True, "llamacpp"
    if deps.cfg.external_enabled and (deps.cfg.anthropic_zdr_confirmed
                                      or deps.cfg.openai_zdr_confirmed):
        return True, "external"
    return False, why


# ── Run creation & execution ────────────────────────────────────────────────
def create_run(question: str, *, sensitivity: str = "open",
               deps: Deps | None = None) -> tuple[str, RunState]:
    deps = deps or build_deps()
    decision = classify(question)
    model_mode, mode_detail = detect_model_mode(deps)
    lineage = (attest_all(deps.router.manifest, deep=False)
               if deps.router else {"roles": {}})
    provenance = {"config_digest": deps.cfg.digest(), "mode": mode_detail}

    with transaction() as cur:
        cur.execute(
            """INSERT INTO runs (question, status, budget_envelope,
                                 computed_sensitivity, lineage_attestation_status,
                                 provenance)
               VALUES (%s, 'planning', %s, %s, %s, %s) RETURNING id""",
            (question, Json(decision.envelope), sensitivity,
             Json(lineage), Json(provenance)),
        )
        run_id = str(cur.fetchone()["id"])
        events.emit(cur, "run.created", run_id=run_id,
                    payload={"gate_class": decision.gate_class,
                             "confidence": decision.confidence,
                             "model_mode": model_mode, "mode": mode_detail})

    state = initial_state(run_id=run_id, question=question, sensitivity=sensitivity,
                          gate_class=decision.gate_class, envelope=decision.envelope,
                          model_mode=model_mode, lineage=lineage, provenance=provenance)
    return run_id, state


def _checkpointer_dsn(cfg: StaticConfig) -> str:
    """LangGraph's saver creates its own ``checkpoints`` tables, whose shape
    collides with the canonical §7.2 ``checkpoints`` table. The saver is
    therefore isolated in schema ``eros_checkpoints`` via search_path
    (amendment A6, db/AMENDMENTS.md); the canonical table remains intact."""
    from urllib.parse import quote

    opt = "-c search_path=eros_checkpoints"
    if cfg.dsn.startswith(("postgresql://", "postgres://")):
        sep = "&" if "?" in cfg.dsn else "?"
        return f"{cfg.dsn}{sep}options={quote(opt, safe='')}"
    return f"{cfg.dsn} options='{opt}'"


def run_graph(run_id: str, state: RunState | None = None,
              deps: Deps | None = None) -> None:
    """Execute (or resume, when state is None) a run to its next terminal."""
    deps = deps or build_deps()
    cfg = deps.cfg
    graph = build_graph(deps)
    with transaction() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS eros_checkpoints")
    with PostgresSaver.from_conn_string(_checkpointer_dsn(cfg)) as saver:
        saver.setup()
        compiled = graph.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 50}
        try:
            compiled.invoke(state, config)  # state=None → resume from checkpoint
        except Exception as e:
            logger.exception("run %s failed: %s", run_id, e)
            with transaction() as cur:
                events.emit(cur, "run.failed", run_id=run_id, payload={"error": str(e)})
                try:
                    cur.execute("UPDATE runs SET status = 'failed' WHERE id = %s", (run_id,))
                except Exception:
                    pass
