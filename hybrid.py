"""Hybrid retrieval — canonical §6.5 Retriever + Evidence Sufficiency Gate.

Ranking is vector similarity + BM25(FTS), fused by reciprocal-rank fusion,
then (optionally) reranked. **C11: trust never influences retrieval** —
``trust_seed`` appears nowhere in this module, and tests/test_core.py greps
the source to keep it that way.

Evidence Sufficiency Gate (FR9): fewer than ``evidence_min_chunks`` results
above threshold raises NoEvidenceFound; the workflow then either re-plans
(bounded, ≤2) or terminates honestly as ``insufficient_evidence``. It never
synthesizes from nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from eros.config import get_static
from eros.errors import NoEvidenceFound
from eros.ingest.processing import Embedder

RRF_K = 60  # standard reciprocal-rank fusion constant [judgment]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    artifact_id: str
    locator: str
    text: str
    score: float
    sensitivity: str


def retrieve(
    cur: psycopg.Cursor,
    query: str,
    *,
    embedder: Embedder | None = None,
    k: int = 12,
    run_scope_artifacts: list[str] | None = None,
) -> list[RetrievedChunk]:
    """RRF over the FTS ranking and (when embeddings exist) the vector ranking."""
    scope_sql, scope_params = "", []
    if run_scope_artifacts:
        scope_sql = "AND c.artifact_id = ANY(%s)"
        scope_params = [run_scope_artifacts]

    rankings: list[list[str]] = []
    rows_by_id: dict[str, dict] = {}

    # FTS / BM25-class ranking
    cur.execute(
        f"""SELECT c.id, c.artifact_id, c.locator, c.text, a.sensitivity,
                   ts_rank_cd(c.fts, websearch_to_tsquery('english', %s)) AS r
            FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
            WHERE c.fts @@ websearch_to_tsquery('english', %s) {scope_sql}
            ORDER BY r DESC LIMIT %s""",
        [query, query, *scope_params, k * 3],
    )
    fts_rows = cur.fetchall()
    rankings.append([str(r["id"]) for r in fts_rows])
    rows_by_id.update({str(r["id"]): r for r in fts_rows})

    # Vector ranking (only when both query and corpus have embeddings)
    qvec = embedder.embed_one(query) if (embedder and embedder.available) else None
    if qvec is not None:
        cur.execute(
            f"""SELECT c.id, c.artifact_id, c.locator, c.text, a.sensitivity
                FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
                WHERE c.embedding IS NOT NULL {scope_sql}
                ORDER BY c.embedding <=> %s::vector LIMIT %s""",
            [*scope_params, str(qvec), k * 3],
        )
        vec_rows = cur.fetchall()
        rankings.append([str(r["id"]) for r in vec_rows])
        rows_by_id.update({str(r["id"]): r for r in vec_rows})

    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [
        RetrievedChunk(
            chunk_id=cid,
            artifact_id=str(rows_by_id[cid]["artifact_id"]),
            locator=rows_by_id[cid]["locator"],
            text=rows_by_id[cid]["text"],
            score=score,
            sensitivity=rows_by_id[cid]["sensitivity"],
        )
        for cid, score in ordered
    ]


def assert_sufficient(chunks: list[RetrievedChunk], *, minimum: int | None = None) -> None:
    """Evidence Sufficiency Gate: ≥ N chunks or fail honest (FR9)."""
    minimum = minimum if minimum is not None else get_static().evidence_min_chunks
    if len(chunks) < minimum:
        raise NoEvidenceFound(
            f"evidence sufficiency gate: {len(chunks)} chunk(s) < required {minimum}",
            found=len(chunks), required=minimum,
        )


def computed_sensitivity(chunks: list[RetrievedChunk]) -> str:
    """Per-claim sensitivity = max over supporting evidence (§8.2)."""
    order = {"open": 0, "restricted": 1, "sensitive": 2}
    if not chunks:
        return "open"
    return max((c.sensitivity for c in chunks), key=lambda s: order.get(s, 0))
