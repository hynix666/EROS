"""Graph-shape CI assertions — canonical C9 / §12.

"Static analysis of compiled graph → fail CI if Verifier reachable from
inside Analyst loop." Compiles the real graph with inert dependencies and
asserts the phase-batched shape: nothing downstream of analyze ever routes
back into it; verify is reachable only from analyze; the only loops are the
two bounded ones (retrieve→search re-plan, qa_eval→report revision).
"""
from __future__ import annotations

import pytest

from eros.gate.heuristic import classify
from eros.governor.budget import Governor
from eros.ingest.processing import Embedder
from eros.pipeline.graph import build_graph
from eros.pipeline.nodes import Deps

pytestmark = pytest.mark.shape

DOWNSTREAM_OF_ANALYZE = {"verify", "arbitrate", "report", "qa_eval", "finalize"}


@pytest.fixture(scope="module")
def edges():
    deps = Deps(cfg=__import__("eros.config", fromlist=["get_static"]).get_static(),
                governor=Governor(), embedder=Embedder(), connectors=[], router=None)
    compiled = build_graph(deps).compile()
    return [(e.source, e.target) for e in compiled.get_graph().edges]


def test_no_path_reenters_analyze(edges):
    offenders = [(s, t) for s, t in edges if t == "analyze" and s in DOWNSTREAM_OF_ANALYZE]
    assert not offenders, f"C9 violated: verification phase routes back into analyze: {offenders}"


def test_verify_only_reachable_from_analyze(edges):
    sources = {s for s, t in edges if t == "verify"}
    assert sources <= {"analyze"}, f"verify reachable from {sources - {'analyze'}}"


def test_bounded_loops_are_the_only_backedges(edges):
    order = ["plan", "search", "ingest", "retrieve", "analyze",
             "verify", "arbitrate", "report", "qa_eval", "finalize"]
    pos = {n: i for i, n in enumerate(order)}
    backedges = {(s, t) for s, t in edges
                 if s in pos and t in pos and pos[t] < pos[s]}
    assert backedges <= {("retrieve", "search"), ("qa_eval", "report")}, (
        f"unexpected back-edges (unbounded loop risk): "
        f"{backedges - {('retrieve', 'search'), ('qa_eval', 'report')}}"
    )


def test_replan_edge_exists(edges):
    assert ("retrieve", "search") in edges, "bounded re-plan path missing (FR9)"


def test_insufficient_evidence_terminal_exists(edges):
    assert any(s == "retrieve" and t == "__end__" for s, t in edges), (
        "retrieve must be able to terminate honestly (insufficient_evidence)"
    )


def test_gate_confidence_floor_defaults_full_investigation():
    d = classify("thoughts on stuff")  # weak signal → conf 0.60 < 0.70 floor
    assert d.gate_class == "full_investigation"
    assert "floor" in d.rationale
