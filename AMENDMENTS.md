# Schema Amendments Ledger

The canonical §7.2 DDL is applied verbatim. Everything beyond it is an
amendment, listed here per the §0.2 governance rule. Each entry states
the problem, the change, and why "do nothing" was rejected.

## A1 — Trust-chain trigger materialization (proposed ADR-023)
**Problem:** §6.8 names triggers g00/g10/g20/g30 and the g05 attested
cross-family constraint, §12 fixes their error strings, Appendix C
requires them attached — but the canonical document contains no DDL for
them, while §0.1 forbids unexecuted DDL from being canonical.
**Change:** Materialized in `schema.sql` under "TRUST-CHAIN DDL
MATERIALIZATION": `run_status_transitions` (data-driven legal state
machine), g00 transition guard, g01 canonical Gate-1 message, g05
attested-XFAM trigger, g10/g20/g30 publish gates, and
`gate_operating_point` with the named `target_must_be_achievable` CHECK.
g20 honors the `eros.gate4_mode` GUC ('shadow' records without blocking,
per §6.8.1 pre-M7 posture). g30 additionally refuses a publish with zero
sentences — coverage of an empty report is vacuous, not 1.0 [judgment].
**Do nothing rejected:** the negative suite (§12, 15/15) cannot hold
against triggers that don't exist.

## A2 — `GRANT SELECT ON draft_claim_evidence TO eros_verifier`
Canonical grants give the Verifier column-scoped UPDATE but no SELECT;
the promotion path (SELECT pending drafts, verify, promote) is otherwise
impossible under the role. Read-only widening; the maker–checker
boundary (Analyst cannot write `claim_evidence`) is untouched.

## A3 — `GRANT DELETE ON claims TO eros_verifier`
Gate 2 ("every claim in the run carries verified evidence") and Gate 4
(run-level UNGROUNDED block) require claims that fail verification to be
*removed from the run*, with provenance retained in
`draft_claim_evidence.status='rejected'` (the enum's purpose). Without
DELETE, one proven-ungrounded claim would wedge every publish forever.

## A4 — `GRANT SELECT ON runs TO eros_verifier`
The g05 trigger executes with the *inserting role's* privileges and reads
`runs.lineage_attestation_status`; without SELECT the trigger itself
fails with permission denied, turning a trust check into an outage.

## A5 — `GRANT DELETE ON report_sentences TO eros_reporter`
FR13 (idempotent or compensating nodes): a resumed or revised Reporter
regenerates its sentence ledger whole (DELETE + INSERT). The alternative
— per-ordinal upsert — needs UPDATE anyway and leaves stale tails.

## A6 — LangGraph checkpointer isolated in schema `eros_checkpoints`
LangGraph's `PostgresSaver` creates its own `checkpoints` tables whose
shape collides with the canonical §7.2 `checkpoints` table (its
`IF NOT EXISTS` no-ops on ours, later migrations then reference
`thread_id`). The saver runs with `search_path=eros_checkpoints`
(created on demand); the canonical table remains intact and reserved.
Checkpoint persistence itself is thereby delegated to LangGraph per
ADR-001 ("LangGraph with Postgres checkpointing").
