"""Heuristic Gate — canonical §6.2.

Classifies each request *before* expensive machinery starts and attaches the
initial budget envelope. Mandatory. Phase 1 ships the deterministic rules
fast-path; the Phi-4-mini classifier slots in behind the same interface when
the CPU Classifier Service is operated (its accuracy then feeds the ADR-018
gate economics — the release target is *derived from measured ROC*, and the
gate_operating_point table refuses infeasible targets).

Canonical rule: confidence < 0.70 → default to full_investigation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from eros.config import get_static

GateClass = Literal["corpus_retrieval", "single_model", "full_investigation"]

_CORPUS_MARKERS = re.compile(
    r"\b(my corpus|already ingested|local evidence|in my (documents|corpus|library)|previously (fetched|ingested))\b",
    re.I,
)
_INVESTIGATION_MARKERS = re.compile(
    r"\b(compare|versus|vs\.?|impact|implications?|trend|landscape|comprehensive|"
    r"deep dive|state of the art|literature|survey|why|how (do|does|did|has|have)|analy[sz]e)\b",
    re.I,
)
_LOOKUP_MARKERS = re.compile(r"^\s*(what is|who is|when (was|did)|define|definition of)\b", re.I)


@dataclass(frozen=True)
class GateDecision:
    gate_class: GateClass
    confidence: float
    envelope: dict = field(default_factory=dict)
    rationale: str = ""


def classify(question: str) -> GateDecision:
    cfg = get_static()
    q = question.strip()
    words = len(q.split())

    if _CORPUS_MARKERS.search(q):
        cls, conf, why = "corpus_retrieval", 0.85, "explicit corpus-scope marker"
    elif _INVESTIGATION_MARKERS.search(q) or words > 25:
        cls, conf, why = "full_investigation", 0.90, "comparative/multi-hop markers or long question"
    elif _LOOKUP_MARKERS.search(q) and words <= 12:
        cls, conf, why = "single_model", 0.75, "short definitional lookup"
    else:
        cls, conf, why = "full_investigation", 0.60, "no strong marker"

    if conf < cfg.gate_confidence_floor:
        cls, why = "full_investigation", why + " → below confidence floor, defaulting (canonical)"

    envelope = _envelope_for(cls, cfg)
    return GateDecision(cls, conf, envelope, why)


def _envelope_for(cls: GateClass, cfg) -> dict:
    # External-USD slices are per-run allowances *within* the monthly ceiling;
    # the Governor's atomic reserve is what actually enforces the ceiling.
    slices = {"corpus_retrieval": 0.0, "single_model": 0.50, "full_investigation": 2.00}
    sources = {"corpus_retrieval": 0, "single_model": 4, "full_investigation": cfg.max_sources_per_run}
    return {
        "gate_class": cls,
        "external_usd": slices[cls],
        "max_sources": sources[cls],
        "max_replans": cfg.max_replans,
        "escalations_remaining": 1,   # in-session escalation: at most one (§6.2)
    }
