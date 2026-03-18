from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from engram import __version__, config
from engram.db import Database
from engram.doctor import all_required_ok, run as run_doctor
from engram.errors import ProjectNotInitializedError
from engram.project import sync_project
from engram.query import (
    build_context,
    delete_memory,
    get_applicable_rules,
    get_project_snapshot,
    list_memory,
    search_documents,
    search_memory,
    store_memory,
)


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _require_str(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _optional_str(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _optional_int(arguments: dict[str, Any], name: str, default: int) -> int:
    value = arguments.get(name)
    return int(value) if isinstance(value, int) else default


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
    except ProjectNotInitializedError as exc:
        return {
            "status": "not_initialized",
            "repo_root": exc.repo_root,
            "message": (
                f"Project '{exc.repo_root}' has not been initialized yet. "
                f"Run `engram auto-init {exc.repo_root}` for idempotent setup or "
                f"`engram init {exc.repo_root} --seed-claude` for a full bootstrap."
            ),
        }
    finally:
        db.close()


def _project_show_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    return get_project_snapshot(db, Path(_require_str(arguments, "repo")))


def _rules_show_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    return get_applicable_rules(
        db=db,
        repo_root=Path(_require_str(arguments, "repo")),
        target_path=_optional_str(arguments, "path"),
        agent=_optional_str(arguments, "agent"),
        branch=_optional_str(arguments, "branch"),
        session_key=_optional_str(arguments, "session"),
    )


def _memory_search_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    return search_memory(
        db=db,
        repo_root=Path(_require_str(arguments, "repo")),
        query=_require_str(arguments, "query"),
        kind=_optional_str(arguments, "kind"),
        limit=_optional_int(arguments, "limit", 10),
    )


def _document_search_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    return search_documents(
        db=db,
        repo_root=Path(_require_str(arguments, "repo")),
        query=_require_str(arguments, "query"),
        doc_type=_optional_str(arguments, "doc_type"),
        limit=_optional_int(arguments, "limit", 10),
    )


def _context_build_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    return build_context(
        db=db,
        repo_root=Path(_require_str(arguments, "repo")),
        query=_require_str(arguments, "query"),
        target_path=_optional_str(arguments, "path"),
        agent=_optional_str(arguments, "agent"),
        branch=_optional_str(arguments, "branch"),
        session_key=_optional_str(arguments, "session"),
        memory_limit=_optional_int(arguments, "memory_limit", 5),
        doc_limit=_optional_int(arguments, "doc_limit", 5),
    )


def _project_sync_tool(_: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    repo = Path(_require_str(arguments, "repo"))
    since_days = arguments.get("since_days")
    if since_days is not None and not isinstance(since_days, int):
        raise ValueError("since_days must be an integer")
    result = sync_project(
        repo_root=repo,
        seed_claude=bool(arguments.get("seed_claude", True)),
        include_subagents=bool(arguments.get("include_subagents", False)),
        since_days=since_days if isinstance(since_days, int) else 180,
    )
    return {
        "repo_root": str(result.repo_root),
        "project_id": result.project_id,
        "docs_indexed": result.docs_indexed,
        "rules_indexed": result.rules_indexed,
        "sessions_imported": result.import_result.sessions_imported,
        "sessions_skipped": result.import_result.sessions_skipped,
        "events_imported": result.import_result.events_imported,
        "command_memories_added": result.import_result.command_memories_added,
        "preference_memories_added": result.import_result.preference_memories_added,
        "summaries_written": [str(path) for path in result.summaries_written],
    }


def _memory_list_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    return list_memory(
        db=db,
        repo_root=Path(_require_str(arguments, "repo")),
        kind=_optional_str(arguments, "kind"),
    )


def _memory_store_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    return store_memory(
        db=db,
        repo_root=Path(_require_str(arguments, "repo")),
        kind=_require_str(arguments, "kind"),
        title=_require_str(arguments, "title"),
        body=_require_str(arguments, "body"),
        source_context=_optional_str(arguments, "source_context"),
    )


def _memory_delete_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    memory_id = arguments.get("memory_id")
    if not isinstance(memory_id, int):
        raise ValueError("memory_id is required")
    return delete_memory(
        db=db,
        repo_root=Path(_require_str(arguments, "repo")),
        memory_id=memory_id,
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
    "project_sync": (
        lambda arguments: _with_db(_project_sync_tool, arguments),
        {
            "description": "Initialize or refresh project state, documents, rules, summaries, and optional Claude imports.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "seed_claude": {"type": "boolean"},
                    "include_subagents": {"type": "boolean"},
                    "since_days": {"type": "integer", "minimum": 0},
                },
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
    "memory_list": (
        lambda arguments: _with_db(_memory_list_tool, arguments),
        {
            "description": "List stored memory for a project, optionally filtered by kind.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "kind": {"type": "string"},
                },
                "required": ["repo"],
                "additionalProperties": False,
            },
        },
    ),
    "memory_store": (
        lambda arguments: _with_db(_memory_store_tool, arguments),
        {
            "description": "Store a project memory item such as a note, lesson, or preference.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "kind": {"type": "string"},
                    "title": {"type": "string", "maxLength": 80},
                    "body": {"type": "string", "maxLength": 2000},
                    "source_context": {"type": "string", "maxLength": 500},
                },
                "required": ["repo", "kind", "title", "body"],
                "additionalProperties": False,
            },
        },
    ),
    "memory_delete": (
        lambda arguments: _with_db(_memory_delete_tool, arguments),
        {
            "description": "Delete a stored project memory item by ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "memory_id": {"type": "integer"},
                },
                "required": ["repo", "memory_id"],
                "additionalProperties": False,
            },
        },
    ),
    "document_search": (
        lambda arguments: _with_db(_document_search_tool, arguments),
        {
            "description": "Search indexed repository documents such as READMEs, manifests, and rules.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "query": {"type": "string"},
                    "doc_type": {"type": "string"},
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
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        text = line.decode("utf-8").strip()
        if not text:
            continue
        if text.startswith("{"):
            return json.loads(text)
        # Content-Length framing: skip headers until blank line, then read body
        headers: dict[str, str] = {}
        if ":" in text:
            name, value = text.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        while True:
            header_line = sys.stdin.buffer.readline()
            if not header_line:
                return None
            if header_line.strip() == b"":
                break
            decoded = header_line.decode("utf-8").strip()
            if ":" in decoded:
                name, value = decoded.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        length = headers.get("content-length")
        if not length:
            continue
        body = sys.stdin.buffer.read(int(length))
        if not body:
            return None
        return json.loads(body.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.write(b"\n")
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
            client_version = (message.get("params") or {}).get("protocolVersion", "2024-11-05")
            _write_message(
                _success(
                    message_id,
                    {
                        "protocolVersion": client_version,
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
