#!/usr/bin/env python3
"""§0.1 Executable Specification Mandate — `make verify-spec`.

Extracts every fenced ```sql block from the canonical document and applies
them, in order, to a scratch database with fail-on-first-error semantics.
Roles are cluster-level, so this expects a fresh cluster (the compose
container qualifies); --dry-run lists the blocks without applying.
"""
import argparse
import pathlib
import re
import sys
import uuid

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "EROS_v3_2_Canonical_Architecture.md"


def blocks(text: str) -> list[str]:
    return re.findall(r"```sql\n(.*?)```", text, re.S)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://eros:eros@127.0.0.1:5432/eros")
    ap.add_argument("--dim", type=int, default=1024)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    found = blocks(DOC.read_text())
    print(f"canonical doc: {len(found)} fenced sql block(s)")
    if args.dry_run:
        for i, b in enumerate(found):
            head = b.strip().splitlines()[0][:70] if b.strip() else "(empty)"
            print(f"  [{i}] {len(b.splitlines())} lines · {head}")
        return 0

    scratch = f"eros_spec_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(args.dsn, autocommit=True)
    admin.execute(f'CREATE DATABASE "{scratch}"')
    scratch_dsn = re.sub(r"/[^/]+$", f"/{scratch}", args.dsn)
    ok = True
    try:
        with psycopg.connect(scratch_dsn) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            for i, b in enumerate(found):
                sql = b.replace("{{ EMBEDDING_DIM }}", str(args.dim))
                try:
                    conn.execute(sql)
                except psycopg.Error as e:
                    print(f"BLOCK {i} FAILED: {e}", file=sys.stderr)
                    ok = False
                    break
            conn.commit() if ok else conn.rollback()
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        admin.close()
    print("verify-spec:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
