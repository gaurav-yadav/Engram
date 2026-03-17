from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from engram import __version__, config
from engram.db import Database
from engram.doctor import all_required_ok, run as run_doctor
from engram.query import build_context, get_applicable_rules, get_project_snapshot, search_memory


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _doctor_tool(_: dict[str, Any]) -> dict[str, Any]:
    checks = run_doctor()
    return {
        "ok": all_required_ok(checks),
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "detail": check.detail,
                "required": check.required,
            }
            for check in checks
        ],
    }


def _with_db(handler: Callable[[Database, dict[str, Any]], dict[str, Any]], arguments: dict[str, Any]) -> dict[str, Any]:
    db = Database(config.db_path())
    db.migrate()
    try:
        return handler(db, arguments)
    finally:
        db.close()


def _project_show_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    repo = arguments.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo is required")
    return get_project_snapshot(db, Path(repo))


def _rules_show_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    repo = arguments.get("repo")
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo is required")
    return get_applicable_rules(
        db=db,
        repo_root=Path(repo),
        target_path=arguments.get("path") if isinstance(arguments.get("path"), str) else None,
        agent=arguments.get("agent") if isinstance(arguments.get("agent"), str) else None,
        branch=arguments.get("branch") if isinstance(arguments.get("branch"), str) else None,
        session_key=arguments.get("session") if isinstance(arguments.get("session"), str) else None,
    )


def _memory_search_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    repo = arguments.get("repo")
    query = arguments.get("query")
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo is required")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    kind = arguments.get("kind") if isinstance(arguments.get("kind"), str) else None
    limit = arguments.get("limit")
    return search_memory(
        db=db,
        repo_root=Path(repo),
        query=query,
        kind=kind,
        limit=int(limit) if isinstance(limit, int) else 10,
    )


def _context_build_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    repo = arguments.get("repo")
    query = arguments.get("query")
    if not isinstance(repo, str) or not repo.strip():
        raise ValueError("repo is required")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required")
    return build_context(
        db=db,
        repo_root=Path(repo),
        query=query,
        target_path=arguments.get("path") if isinstance(arguments.get("path"), str) else None,
        agent=arguments.get("agent") if isinstance(arguments.get("agent"), str) else None,
        branch=arguments.get("branch") if isinstance(arguments.get("branch"), str) else None,
        session_key=arguments.get("session") if isinstance(arguments.get("session"), str) else None,
        memory_limit=int(arguments["memory_limit"]) if isinstance(arguments.get("memory_limit"), int) else 5,
        doc_limit=int(arguments["doc_limit"]) if isinstance(arguments.get("doc_limit"), int) else 5,
    )


TOOLS: dict[str, tuple[ToolHandler, dict[str, Any]]] = {
    "doctor": (
        _doctor_tool,
        {
            "description": "Validate local engram runtime requirements.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ),
    "project_show": (
        lambda arguments: _with_db(_project_show_tool, arguments),
        {
            "description": "Show project stats and summaries for an initialized repository.",
            "inputSchema": {
                "type": "object",
                "properties": {"repo": {"type": "string"}},
                "required": ["repo"],
                "additionalProperties": False,
            },
        },
    ),
    "rules_show": (
        lambda arguments: _with_db(_rules_show_tool, arguments),
        {
            "description": "Resolve deterministic rules for a repo, path, agent, branch, or session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "path": {"type": "string"},
                    "agent": {"type": "string"},
                    "branch": {"type": "string"},
                    "session": {"type": "string"},
                },
                "required": ["repo"],
                "additionalProperties": False,
            },
        },
    ),
    "memory_search": (
        lambda arguments: _with_db(_memory_search_tool, arguments),
        {
            "description": "Search distilled repo memory such as commands and user preferences.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "query": {"type": "string"},
                    "kind": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["repo", "query"],
                "additionalProperties": False,
            },
        },
    ),
    "context_build": (
        lambda arguments: _with_db(_context_build_tool, arguments),
        {
            "description": "Build a coding context bundle from summaries, scoped rules, memory, and indexed docs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "agent": {"type": "string"},
                    "branch": {"type": "string"},
                    "session": {"type": "string"},
                    "memory_limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "doc_limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["repo", "query"],
                "additionalProperties": False,
            },
        },
    ),
}


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        header = line.decode("utf-8").strip()
        if ":" not in header:
            continue
        name, value = header.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    length = headers.get("content-length")
    if not length:
        return None
    body = sys.stdin.buffer.read(int(length))
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def _tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    result = {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
    }
    if is_error:
        result["isError"] = True
    return result


def run_stdio_server() -> None:
    while True:
        message = _read_message()
        if message is None:
            return
        method = message.get("method")
        message_id = message.get("id")

        if method == "initialize":
            _write_message(
                _success(
                    message_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "engram", "version": __version__},
                    },
                ),
            )
            continue

        if method == "notifications/initialized":
            continue

        if method == "ping":
            _write_message(_success(message_id, {}))
            continue

        if method == "tools/list":
            tools = [
                {
                    "name": name,
                    "description": metadata["description"],
                    "inputSchema": metadata["inputSchema"],
                }
                for name, (_, metadata) in TOOLS.items()
            ]
            _write_message(_success(message_id, {"tools": tools}))
            continue

        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or name not in TOOLS:
                _write_message(_success(message_id, _tool_result({"error": "unknown_tool"}, is_error=True)))
                continue
            if not isinstance(arguments, dict):
                _write_message(_success(message_id, _tool_result({"error": "arguments must be an object"}, is_error=True)))
                continue
            handler = TOOLS[name][0]
            try:
                payload = handler(arguments)
                _write_message(_success(message_id, _tool_result(payload)))
            except Exception as exc:
                _write_message(_success(message_id, _tool_result({"error": str(exc)}, is_error=True)))
            continue

        if method == "shutdown":
            _write_message(_success(message_id, {}))
            return

        if message_id is not None:
            _write_message(_error(message_id, -32601, f"method not found: {method}"))
