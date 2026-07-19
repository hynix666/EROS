"""MCP server — canonical §6.1 MCP Server Contract.

stdio transport, JSON Schema Draft 2020-12 tool schemas. Tools:
``research_start``, ``research_status``, ``evidence_query``. Sensitivity is
enforced at the tool boundary; a sensitive run initiation is audited as
``MCP_SENSITIVE_RUN_INITIATED`` (canonical).

Deliberately dependency-free (raw JSON-RPC over stdin/stdout) so the same
binary works under any MCP host on Windows, Linux, and macOS.
"""
from __future__ import annotations

import json
import logging
import sys

from psycopg.types.json import Json
import threading

from eros.db.pool import transaction
from eros.lil import events
from eros.pipeline.graph import build_deps, create_run, run_graph
from eros.retrieval import hybrid

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
logger = logging.getLogger("eros.mcp")

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "research_start",
        "description": "Start an autonomous EROS research run for a question. "
                       "Returns the run_id to poll with research_status.",
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 3},
                "sensitivity": {"type": "string",
                                "enum": ["open", "restricted", "sensitive"],
                                "default": "open"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "research_status",
        "description": "Status, telemetry, and (when available) the report of a run.",
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "evidence_query",
        "description": "Hybrid (FTS + vector) search over the ingested evidence corpus.",
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "k": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8},
            },
            "required": ["query"],
        },
    },
]


class McpServer:
    def __init__(self) -> None:
        self.deps = build_deps()

    # ── tool implementations ───────────────────────────────────────────────
    def research_start(self, args: dict) -> dict:
        question = args["question"]
        sensitivity = args.get("sensitivity", "open")
        run_id, state = create_run(question, sensitivity=sensitivity, deps=self.deps)
        if sensitivity == "sensitive":
            with transaction() as cur:
                cur.execute(
                    """INSERT INTO audit (event_type, actor, action, metadata)
                       VALUES ('MCP_SENSITIVE_RUN_INITIATED', 'mcp', 'research_start', %s)""",
                    (Json({"run_id": run_id}),),
                )
                events.emit(cur, "MCP_SENSITIVE_RUN_INITIATED", run_id=run_id)
        threading.Thread(target=run_graph, args=(run_id, state),
                         kwargs={"deps": self.deps}, daemon=True).start()
        return {"run_id": run_id, "status": "started"}

    def research_status(self, args: dict) -> dict:
        run_id = args["run_id"]
        with transaction() as cur:
            cur.execute(
                """SELECT status, question, computed_sensitivity, created_at
                   FROM runs WHERE id = %s""", (run_id,))
            run = cur.fetchone()
            if run is None:
                return {"error": "run not found"}
            tel = events.run_details(cur, run_id)[-10:]
            cur.execute(
                """SELECT ordinal, kind, text FROM report_sentences
                   WHERE run_id = %s ORDER BY ordinal""", (run_id,))
            sentences = cur.fetchall()
        return {
            "status": run["status"],
            "question": run["question"],
            "sensitivity": run["computed_sensitivity"],
            "recent_events": [{"type": t["event_type"],
                               "at": t["created_at"].isoformat()} for t in tel],
            "report": ([s["text"] for s in sentences] if sentences else None),
        }

    def evidence_query(self, args: dict) -> dict:
        with transaction() as cur:
            chunks = hybrid.retrieve(cur, args["query"], embedder=self.deps.embedder,
                                     k=int(args.get("k", 8)))
        return {"results": [{"chunk_id": c.chunk_id, "locator": c.locator,
                             "score": round(c.score, 4),
                             "sensitivity": c.sensitivity,
                             "text": c.text[:600]} for c in chunks]}

    # ── JSON-RPC plumbing ──────────────────────────────────────────────────
    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            return self._result(mid, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "eros", "version": "3.2.0"},
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self._result(mid, {"tools": TOOLS})
        if method == "tools/call":
            name = msg.get("params", {}).get("name")
            args = msg.get("params", {}).get("arguments", {}) or {}
            fn = {"research_start": self.research_start,
                  "research_status": self.research_status,
                  "evidence_query": self.evidence_query}.get(name)
            if fn is None:
                return self._error(mid, -32602, f"unknown tool {name!r}")
            try:
                out = fn(args)
                return self._result(mid, {"content": [
                    {"type": "text", "text": json.dumps(out, default=str, indent=2)}]})
            except Exception as e:
                logger.exception("tool %s failed", name)
                return self._result(mid, {"isError": True, "content": [
                    {"type": "text", "text": f"{type(e).__name__}: {e}"}]})
        if mid is not None:
            return self._error(mid, -32601, f"method {method!r} not found")
        return None

    @staticmethod
    def _result(mid, result) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid, code, message) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main() -> None:
    server = McpServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = server.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
