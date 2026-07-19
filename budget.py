"""Budget Governor — canonical §6.4.

* $50/month default external ceiling; local inference is free (tracked, not gated).
* **Atomic reserve**: one statement; zero rows returned = reservation refused.
  The budget row is taken FOR UPDATE, so concurrent reservations against a
  shared ceiling serialize and cannot breach it.
* **Synchronous release**: settle() records actual spend and releases
  (estimated − actual) in the same transaction — implicitly, because held
  headroom only counts rows still in status='reserved'.
* **Idempotency**: keys are SHA256(run_id ‖ node ‖ attempt ‖ prompt_digest)
  (§6.9), written to the ledger *before* dispatch. On resume: settled →
  re-read the response from events (never re-issue); reserved with a
  provider_request_id → orphan and alert; no row → dispatch.
* **Startup reconciliation** applies exactly the canonical rules.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import psycopg
from psycopg.types.json import Json

from eros.config import get_static
from eros.errors import BudgetReservationFailed
from eros.lil import events


def current_period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def idempotency_key(run_id: str, node: str, attempt: int, prompt_digest: str) -> str:
    material = "\u2016".join([run_id, node, str(attempt), prompt_digest])  # ‖ per §6.9
    return hashlib.sha256(material.encode()).hexdigest()


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


@dataclass(frozen=True)
class Reservation:
    id: str
    status: str  # reserved | settled | released | orphaned
    estimated_cost: Decimal


class Governor:
    def __init__(self, ceiling_usd: float | None = None) -> None:
        self.ceiling = Decimal(str(ceiling_usd if ceiling_usd is not None else get_static().budget_ceiling_usd))

    # ── period bootstrap ───────────────────────────────────────────────────
    def ensure_period(self, cur: psycopg.Cursor, period: str | None = None) -> str:
        period = period or current_period()
        cur.execute(
            """INSERT INTO budgets (period, external_ceiling)
               VALUES (%s, %s) ON CONFLICT (period) DO NOTHING""",
            (period, self.ceiling),
        )
        return period

    # ── atomic reserve ─────────────────────────────────────────────────────
    def reserve(
        self,
        cur: psycopg.Cursor,
        *,
        run_id: str,
        node_name: str,
        idem_key: str,
        estimated_cost: float,
        provider: str,
        period: str | None = None,
    ) -> Reservation:
        """Reserve headroom or raise BudgetReservationFailed.

        Idempotent on idem_key: a resumed node receives its existing
        reservation (whatever its status) instead of double-reserving.
        """
        period = self.ensure_period(cur, period)

        cur.execute(
            "SELECT id, status, estimated_cost FROM budget_reservations WHERE idempotency_key = %s",
            (idem_key,),
        )
        existing = cur.fetchone()
        if existing:
            return Reservation(str(existing["id"]), existing["status"], existing["estimated_cost"])

        est = Decimal(str(estimated_cost))
        cur.execute(
            """
            WITH b AS (
                SELECT id, external_ceiling, external_consumed
                FROM budgets WHERE period = %s
                FOR UPDATE
            ),
            held AS (
                SELECT COALESCE(SUM(br.estimated_cost), 0) AS h
                FROM budget_reservations br JOIN b ON br.budget_id = b.id
                WHERE br.status = 'reserved'
            )
            INSERT INTO budget_reservations
                (budget_id, run_id, node_name, idempotency_key, estimated_cost, provider, status)
            SELECT b.id, %s, %s, %s, %s, %s, 'reserved'
            FROM b, held
            WHERE b.external_consumed + held.h + %s <= b.external_ceiling
            RETURNING id
            """,
            (period, run_id, node_name, idem_key, est, provider, est),
        )
        row = cur.fetchone()
        if row is None:  # zero rows returned = reservation refused (canonical)
            events.emit(cur, "budget.reservation_refused", run_id=run_id,
                        payload={"node": node_name, "estimated": float(est), "provider": provider})
            raise BudgetReservationFailed(
                f"budget ceiling would be breached by ${est} for {node_name}",
                run_id=run_id, node=node_name,
            )
        rid = str(row["id"])
        events.emit(cur, "budget.reserved", run_id=run_id,
                    payload={"reservation_id": rid, "node": node_name,
                             "estimated": float(est), "provider": provider})
        return Reservation(rid, "reserved", est)

    # ── dispatch bookkeeping (written BEFORE the external call goes out) ───
    def record_dispatch(self, cur: psycopg.Cursor, reservation_id: str, provider_request_id: str) -> None:
        cur.execute(
            "UPDATE budget_reservations SET provider_request_id = %s WHERE id = %s AND status = 'reserved'",
            (provider_request_id, reservation_id),
        )

    # ── synchronous settle: actual spend + implicit release, one txn ───────
    def settle(self, cur: psycopg.Cursor, reservation_id: str, actual_cost: float) -> None:
        actual = Decimal(str(actual_cost))
        cur.execute(
            """UPDATE budget_reservations
               SET status = 'settled', actual_cost = %s, settled_at = NOW()
               WHERE id = %s AND status = 'reserved'
               RETURNING budget_id, run_id, estimated_cost""",
            (actual, reservation_id),
        )
        row = cur.fetchone()
        if row is None:
            return  # already settled on a previous attempt — idempotent
        cur.execute(
            "UPDATE budgets SET external_consumed = external_consumed + %s WHERE id = %s",
            (actual, row["budget_id"]),
        )
        events.emit(cur, "budget.settled", run_id=str(row["run_id"]),
                    payload={"reservation_id": reservation_id, "actual": float(actual),
                             "refund": float(row["estimated_cost"] - actual)})

    def release(self, cur: psycopg.Cursor, reservation_id: str) -> None:
        """Unspent reservation returned to the pool (cancel / never dispatched)."""
        cur.execute(
            """UPDATE budget_reservations SET status = 'released'
               WHERE id = %s AND status = 'reserved' RETURNING run_id""",
            (reservation_id,),
        )
        row = cur.fetchone()
        if row:
            events.emit(cur, "budget.released", run_id=str(row["run_id"]),
                        payload={"reservation_id": reservation_id})

    def release_run(self, cur: psycopg.Cursor, run_id: str) -> int:
        """Cooperative cancellation: release every unspent reservation of a run."""
        cur.execute(
            """UPDATE budget_reservations SET status = 'released'
               WHERE run_id = %s AND status = 'reserved' AND provider_request_id IS NULL""",
            (run_id,),
        )
        return cur.rowcount

    # ── startup reconciliation (canonical §6.4 / §6.9) ─────────────────────
    def reconcile_startup(self, cur: psycopg.Cursor) -> dict[str, int]:
        cur.execute(
            """UPDATE budget_reservations SET status = 'released'
               WHERE status = 'reserved' AND provider_request_id IS NULL"""
        )
        released = cur.rowcount
        cur.execute(
            """UPDATE budget_reservations SET status = 'orphaned'
               WHERE status = 'reserved' AND provider_request_id IS NOT NULL
               RETURNING id, run_id, provider, provider_request_id"""
        )
        orphans = cur.fetchall()
        for o in orphans:
            # Possibly billed — alert the operator; NEVER blindly retry.
            events.emit(cur, "budget.reservation_orphaned", run_id=str(o["run_id"]),
                        payload={"reservation_id": str(o["id"]), "provider": o["provider"],
                                 "provider_request_id": o["provider_request_id"]})
            cur.execute(
                """INSERT INTO audit (event_type, actor, action, metadata)
                   VALUES ('BUDGET_ORPHAN', 'governor', 'orphaned reservation flagged', %s)""",
                (Json({"reservation_id": str(o["id"]),
                       "provider_request_id": o["provider_request_id"]}),),
            )
        return {"released_undispatched": released, "orphaned": len(orphans)}
