"""Event bus — canonical §6.7.

Events are the LIL's async spine and the flat episodic log for Phase 1.
Publication happens **in the same transaction** that writes the underlying
row: an event either exists with its cause or not at all. ``NOTIFY``
carries only the event id (payloads are fetched from the table by id;
the 8000-byte NOTIFY limit never truncates data). Missed notifications
are UI staleness issues, never data loss — the WS layer tails the table.

The structured telemetry columns (latency_ms, token_count, model_name,
cost_estimate) support the §11.1 "Run Details" query pattern without
relying on traces.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Json

CHANNEL = "eros_events"


def emit(
    cur: psycopg.Cursor,
    event_type: str,
    *,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    token_count: int | None = None,
    model_name: str | None = None,
    cost_estimate: float | None = None,
) -> str:
    """Insert an event and NOTIFY, inside the caller's open transaction."""
    cur.execute(
        """INSERT INTO events (event_type, run_id, payload, latency_ms, token_count, model_name, cost_estimate)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (event_type, run_id, Json(payload or {}), latency_ms, token_count, model_name, cost_estimate),
    )
    event_id = str(cur.fetchone()["id"])
    cur.execute("SELECT pg_notify(%s, %s)", (CHANNEL, event_id))
    return event_id


def tail(
    cur: psycopg.Cursor,
    *,
    run_id: str | None = None,
    after: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Fetch events newer than a watermark (WS polling / Run Details)."""
    clauses, params = ["TRUE"], []
    if run_id is not None:
        clauses.append("run_id = %s")
        params.append(run_id)
    if after is not None:
        clauses.append("created_at > %s")
        params.append(after)
    params.append(limit)
    cur.execute(
        f"""SELECT id, event_type, run_id, payload, latency_ms, token_count,
                    model_name, cost_estimate, created_at
            FROM events WHERE {' AND '.join(clauses)}
            ORDER BY created_at ASC LIMIT %s""",
        params,
    )
    return list(cur.fetchall())


def run_details(cur: psycopg.Cursor, run_id: str) -> list[dict[str, Any]]:
    """Canonical §11.1 Run Details query (Postgres, not Prometheus)."""
    cur.execute(
        """SELECT e.event_type, e.latency_ms, e.token_count, e.model_name, e.cost_estimate, e.created_at
           FROM events e WHERE e.run_id = %s ORDER BY e.created_at""",
        (run_id,),
    )
    return list(cur.fetchall())
