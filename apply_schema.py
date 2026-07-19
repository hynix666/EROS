#!/usr/bin/env python3
"""Apply db/schema.sql with EMBEDDING_DIM interpolated, ON_ERROR_STOP semantics.

The canonical schema carries an {{ EMBEDDING_DIM }} placeholder (§7.2);
this is the single point where it is bound (default 1024 = bge-large-en-v1.5).
"""
import argparse
import pathlib
import sys

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default="postgresql://eros:eros@127.0.0.1:5432/eros")
    ap.add_argument("--dim", type=int, default=1024)
    ap.add_argument("--schema", default=str(ROOT / "db" / "schema.sql"))
    args = ap.parse_args()

    sql = pathlib.Path(args.schema).read_text().replace("{{ EMBEDDING_DIM }}", str(args.dim))
    try:
        with psycopg.connect(args.dsn) as conn:
            conn.execute(sql)   # single script; any error aborts the whole apply
            conn.commit()
    except psycopg.Error as e:
        print(f"SCHEMA APPLY FAILED (fail-closed): {e}", file=sys.stderr)
        return 1
    print(f"schema applied (EMBEDDING_DIM={args.dim})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
