"""Database access — connection factory, role scoping, GPU mutex.

The privilege boundary (ADR-017) is only real if the pipeline actually runs
under the granted roles: each node's transaction executes with
``SET LOCAL ROLE eros_<role>`` so the Analyst is *structurally* unable to
write claim_evidence at runtime, exactly as the negative suite proves.

The GPU mutex (§6.6) is a Postgres transaction-scoped advisory lock
(``pg_advisory_xact_lock``) acquired before any Ollama evict/load call.
"""
from __future__ import annotations

import contextlib
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from eros.config import get_static

# One well-known key for the single GPU (C10: one active run; single card).
GPU_MUTEX_KEY = 0x45524F53  # 'EROS'


def connect(*, autocommit: bool = False) -> psycopg.Connection:
    """New connection with the Gate-4 GUC applied via conninfo options."""
    conn = psycopg.connect(get_static().conninfo(), row_factory=dict_row)
    conn.autocommit = autocommit
    return conn


@contextlib.contextmanager
def transaction(role: str | None = None) -> Iterator[psycopg.Cursor]:
    """One transaction; optionally scoped to a pipeline role.

    ``SET LOCAL ROLE`` reverts automatically at commit/rollback, so a role
    can never leak across node boundaries.
    """
    with connect() as conn:
        with conn.cursor() as cur:
            if role is not None:
                cur.execute(psycopg.sql.SQL("SET LOCAL ROLE {}").format(psycopg.sql.Identifier(role)))
            yield cur
        conn.commit()


@contextlib.contextmanager
def gpu_mutex(cur: psycopg.Cursor) -> Iterator[None]:
    """Advisory transaction lock guarding every evict-then-load (§6.6).

    Held until the surrounding transaction ends — even if the workflow
    engine races, only one slot transition proceeds at a time.
    """
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (GPU_MUTEX_KEY,))
    yield


def fetch_run(cur: psycopg.Cursor, run_id: str) -> dict | None:
    cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
    return cur.fetchone()


def set_run_status(cur: psycopg.Cursor, run_id: str, status: str) -> None:
    """Status transitions go through the DB so g00/g10/g20/g30 always fire."""
    cur.execute("UPDATE runs SET status = %s WHERE id = %s", (status, run_id))


def cancel_requested(cur: psycopg.Cursor, run_id: str) -> bool:
    cur.execute("SELECT cancel_requested FROM runs WHERE id = %s", (run_id,))
    row = cur.fetchone()
    return bool(row and row["cancel_requested"])
