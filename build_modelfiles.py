#!/usr/bin/env python3
"""Create the derived Modelfiles (§6.6 Context Ceiling) from the manifest.

Base tags are blocked at routing time; only eros-* derived tags with a
baked num_ctx are ever loaded. Requires `ollama` on PATH and the base
variants already pulled (`ollama pull <variant>`).
"""
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    manifest = json.loads((ROOT / "config" / "manifest.json").read_text())
    failures = 0
    for role, spec in manifest["models"].items():
        tag, base, ctx = spec["modelfile_tag"], spec["variant"], spec["num_ctx"]
        body = f"FROM {base}\nPARAMETER num_ctx {ctx}\n"
        with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False) as f:
            f.write(body)
            path = f.name
        print(f"[{role}] ollama create {tag}  (FROM {base}, num_ctx {ctx})")
        r = subprocess.run(["ollama", "create", tag, "-f", path])
        failures += r.returncode != 0
    if failures:
        print(f"{failures} model(s) failed — pull the base variants first", file=sys.stderr)
    else:
        print("derived models created; now pin digests: scripts/attest_models.py --pin")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
