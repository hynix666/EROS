#!/usr/bin/env python3
"""Attestation utility (ADR-022 / §6.6).

default : attest every role against the pinned digests (deep SHA-256 of
          the GGUF blob Ollama will load); nonzero exit on any failure.
--pin   : resolve each derived tag's GGUF digest from the Ollama store and
          write it into config/manifest.json (the audited pinning step).
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))
from eros.router.attestation import (  # noqa: E402
    attest_all, load_manifest, resolve_tag_gguf_digest)

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "manifest.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pin", action="store_true")
    args = ap.parse_args()

    if args.pin:
        doc = json.loads(MANIFEST.read_text())
        missing = []
        for role, spec in doc["models"].items():
            digest = resolve_tag_gguf_digest(spec["modelfile_tag"])
            if digest is None:
                missing.append(spec["modelfile_tag"])
                continue
            spec["gguf_digest"] = digest
            print(f"[{role}] {spec['modelfile_tag']} → {digest}")
        if missing:
            print(f"not found in Ollama store: {missing} — run build_modelfiles.py first",
                  file=sys.stderr)
            return 1
        MANIFEST.write_text(json.dumps(doc, indent=2) + "\n")
        print("manifest pinned — record this change in an ADR (ADR-022 process)")
        return 0

    manifest = load_manifest(MANIFEST)
    status = attest_all(manifest, deep=True)
    bad = 0
    for role, rec in status["roles"].items():
        mark = "OK " if rec["attested"] else "FAIL"
        bad += not rec["attested"]
        print(f"[{mark}] {role:8s} {rec['model']:22s} {rec.get('reason', rec['digest'])}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
