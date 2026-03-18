from __future__ import annotations

import argparse
import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from engram import __version__, config
from engram.db import Database
from engram.doctor import all_required_ok, format_checks, run as run_doctor
from engram.errors import ProjectNotInitializedError
from engram.mcp import run_stdio_server
from engram.project import initialize_project, sync_project
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


AUTOINIT_SHELL_SCRIPT = """#!/usr/bin/env bash
set -eu

REPO="${1:-$(pwd)}"
MARKER="$REPO/.engram/project.yaml"

[ -f "$MARKER" ] && exit 0

if command -v engram >/dev/null 2>&1; then
  nohup engram auto-init "$REPO" >/dev/null 2>&1 &
else
  nohup python3 -m engram auto-init "$REPO" >/dev/null 2>&1 &
fi
exit 0
"""


def _parse_since(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value.endswith("d"):
        value = value[:-1]
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("--since must be a number of days, e.g. '90d' or '90'") from exc


def _excerpt(text: str, limit: int = 140) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _infer_repo_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".engram" / "project.yaml").exists():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return None


def _resolve_repo_or_raise(raw_repo: str | None, *, fallback_to_cwd: bool = False) -> Path:
    if raw_repo:
        return Path(raw_repo).expanduser().resolve()
    inferred = _infer_repo_root()
    if inferred is not None:
        return inferred
    if fallback_to_cwd:
        return Path.cwd().resolve()
    raise ValueError("repo is required when not running inside a repository; pass --repo or run the command from the repo root")


def _resolve_repo_and_query(raw_repo: str | None, terms: list[str]) -> tuple[Path, str]:
    if not terms:
        raise ValueError("query is required")

    query_terms = list(terms)
    if raw_repo:
        repo = _resolve_repo_or_raise(raw_repo)
    elif len(query_terms) > 1:
        candidate = Path(query_terms[0]).expanduser()
        if candidate.exists():
            if not candidate.is_dir():
                raise ValueError(f"{candidate} is not a readable repository directory")
            repo = candidate.resolve()
            query_terms = query_terms[1:]
        else:
            repo = _resolve_repo_or_raise(None)
    else:
        repo = _resolve_repo_or_raise(None)

    query = " ".join(query_terms).strip()
    if not query:
        raise ValueError("query is required")
    return repo, query


def _uninitialized_message(repo_root: str) -> str:
    return (
        f"Project '{repo_root}' is not initialized. "
        f"Run `engram auto-init {repo_root}` for idempotent setup or "
        f"`engram init {repo_root} --seed-claude` for a full bootstrap."
    )


def _cmd_doctor(args: argparse.Namespace) -> int:
    repo = _resolve_repo_or_raise(args.repo) if args.repo else _infer_repo_root()
    checks = run_doctor(repo)
    print(format_checks(checks))
    return 0 if all_required_ok(checks) else 1


def _cmd_init(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_or_raise(args.repo, fallback_to_cwd=True)
    result = initialize_project(
        repo_root=repo_root,
        seed_claude=bool(args.seed_claude),
        include_subagents=bool(args.include_subagents),
        since_days=_parse_since(args.since),
    )
    print(f"Initialized project memory for {result.repo_root}")
    print(f"Project ID: {result.project_id}")
    print(f"Indexed documents: {result.docs_indexed}")
    print(f"Indexed rules: {result.rules_indexed}")
    print(
        f"Imported Claude sessions: {result.import_result.sessions_imported} "
        f"(skipped {result.import_result.sessions_skipped})"
    )
    print(f"Imported archive events: {result.import_result.events_imported}")
    print(f"Promoted command memories: {result.import_result.command_memories_added}")
    print(f"Promoted preference memories: {result.import_result.preference_memories_added}")
    if result.summaries_written:
        print("Wrote summaries:")
        for path in result.summaries_written:
            print(f"  - {path}")
    return 0


def _format_sync_result(prefix: str, result: Any) -> str:
    lines = [
        f"{prefix} {result.repo_root}",
        f"Project ID: {result.project_id}",
        f"Indexed documents: {result.docs_indexed}",
        f"Indexed rules: {result.rules_indexed}",
        (
            f"Imported Claude sessions: {result.import_result.sessions_imported} "
            f"(skipped {result.import_result.sessions_skipped})"
        ),
        f"Imported archive events: {result.import_result.events_imported}",
        f"Promoted command memories: {result.import_result.command_memories_added}",
        f"Promoted preference memories: {result.import_result.preference_memories_added}",
    ]
    if result.summaries_written:
        lines.append("Wrote summaries:")
        lines.extend(f"  - {path}" for path in result.summaries_written)
    return "\n".join(lines)


def _db() -> Database:
    db = Database(config.db_path())
    db.migrate()
    return db


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _format_rules(payload: dict[str, Any]) -> str:
    lines = [f"Rules for {payload['repo_root']}"]
    if payload["target_path"]:
        lines.append(f"Target path: {payload['target_path']}")
    if payload["agent"]:
        lines.append(f"Agent: {payload['agent']}")
    if payload["branch"]:
        lines.append(f"Branch: {payload['branch']}")
    if payload["session_key"]:
        lines.append(f"Session: {payload['session_key']}")
    lines.append("")
    if not payload["rules"]:
        lines.append("No applicable rules found.")
        return "\n".join(lines)
    for rule in payload["rules"]:
        lines.append(f"[{rule['scope_type']}] {rule['title']}")
        lines.append(f"Source: {rule['source_path']}")
        lines.append(rule["body"].strip())
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_memory(payload: dict[str, Any]) -> str:
    lines = [f"Memory search for {payload['repo_root']}", f"Query: {payload['query']}", ""]
    if not payload["results"]:
        lines.append("No matching memory items found.")
        return "\n".join(lines)
    for item in payload["results"]:
        lines.append(f"[{item['kind']}] {item['title']}")
        lines.append(f"Scope: {item['scope_type']} {item['scope_key']}")
        lines.append(item["body"].strip())
        if item["provenance"]:
            lines.append("Provenance:")
            for provenance in item["provenance"]:
                excerpt = provenance["source_excerpt"] or ""
                lines.append(
                    f"  - session={provenance['archive_session_id']} event={provenance['archive_event_id']} {excerpt}"
                )
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_project(payload: dict[str, Any]) -> str:
    project = payload["project"]
    stats = payload["stats"]
    lines = [
        f"Project: {project['repo_name']}",
        f"Repo: {project['repo_path']}",
        f"Last synced: {project['last_synced_at']}",
        "",
        "Stats:",
        f"  - documents: {stats['documents_count']}",
        f"  - rules: {stats['rules_count']}",
        f"  - memory items: {stats['memory_count']}",
        f"  - archive sessions: {stats['archive_sessions_count']}",
        f"  - archive events: {stats['archive_events_count']}",
        "",
    ]
    if payload["summaries"]["project"]:
        lines.append(payload["summaries"]["project"].strip())
    else:
        lines.append("No project summary found.")
    return "\n".join(lines).rstrip()


def _format_memory_list(payload: dict[str, Any]) -> str:
    lines = [f"Memory for {payload['repo_root']}"]
    if payload["kind"]:
        lines.append(f"Kind: {payload['kind']}")
    lines.append("")
    if not payload["results"]:
        lines.append("No memory items found.")
        return "\n".join(lines)
    for item in payload["results"]:
        lines.append(f"#{item['id']} [{item['kind']}] {item['title']}")
        lines.append(f"Scope: {item['scope_type']} {item['scope_key']}")
        lines.append(_excerpt(item["body"], limit=220))
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_documents(payload: dict[str, Any]) -> str:
    lines = [f"Document search for {payload['repo_root']}", f"Query: {payload['query']}", ""]
    if not payload["results"]:
        lines.append("No matching documents found.")
        return "\n".join(lines)
    for item in payload["results"]:
        lines.append(f"[{item['doc_type']}] {item['path']}")
        lines.append(_excerpt(item["body"], limit=220))
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_context(payload: dict[str, Any]) -> str:
    lines = [f"Context for {payload['repo_root']}", f"Query: {payload['query']}", ""]
    if payload["summary"]:
        lines.append("Project summary:")
        lines.append(payload["summary"].strip())
        lines.append("")
    lines.append("Applicable rules:")
    if payload["rules"]:
        for rule in payload["rules"]:
            lines.append(f"- [{rule['scope_type']}] {rule['title']} ({rule['source_path']})")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Matching memory:")
    if payload["memory"]:
        for item in payload["memory"]:
            lines.append(f"- [{item['kind']}] {item['title']}: {_excerpt(item['body'])}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Matching documents:")
    if payload["documents"]:
        for doc in payload["documents"]:
            lines.append(f"- [{doc['doc_type']}] {doc['path']}: {_excerpt(doc['body'])}")
    else:
        lines.append("- None")
    return "\n".join(lines).rstrip()


def _cmd_rules_show(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_or_raise(args.repo)
    db = _db()
    try:
        payload = get_applicable_rules(
            db=db,
            repo_root=repo_root,
            target_path=args.path,
            agent=args.agent,
            branch=args.branch,
            session_key=args.session,
        )
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        print(_format_rules(payload))
    return 0


def _cmd_memory_search(args: argparse.Namespace) -> int:
    repo_root, query = _resolve_repo_and_query(args.repo, args.terms)
    db = _db()
    try:
        payload = search_memory(
            db=db,
            repo_root=repo_root,
            query=query,
            kind=args.kind,
            limit=args.limit,
        )
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        print(_format_memory(payload))
    return 0


def _cmd_memory_list(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_or_raise(args.repo)
    db = _db()
    try:
        payload = list_memory(
            db=db,
            repo_root=repo_root,
            kind=args.kind,
        )
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        print(_format_memory_list(payload))
    return 0


def _cmd_memory_store(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_or_raise(args.repo)
    db = _db()
    try:
        payload = store_memory(
            db=db,
            repo_root=repo_root,
            kind=args.kind,
            title=args.title,
            body=args.body,
            source_context=args.source_context,
        )
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        memory = payload["memory"]
        print(f"Stored memory #{memory['id']} [{memory['kind']}] {memory['title']}")
    return 0


def _cmd_memory_delete(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_or_raise(args.repo)
    db = _db()
    try:
        payload = delete_memory(
            db=db,
            repo_root=repo_root,
            memory_id=args.memory_id,
        )
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        if payload["deleted"]:
            print(f"Deleted memory #{payload['memory_id']}")
        else:
            print(f"Memory #{payload['memory_id']} not found")
    return 0


def _cmd_project_show(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_or_raise(args.repo)
    db = _db()
    try:
        payload = get_project_snapshot(db=db, repo_root=repo_root)
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        print(_format_project(payload))
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    repo_root = _resolve_repo_or_raise(args.repo, fallback_to_cwd=True)
    result = sync_project(
        repo_root=repo_root,
        seed_claude=not args.skip_claude,
        include_subagents=bool(args.include_subagents),
        since_days=_parse_since(args.since),
    )
    print(_format_sync_result("Synchronized project memory for", result))
    return 0


def _cmd_docs_search(args: argparse.Namespace) -> int:
    repo_root, query = _resolve_repo_and_query(args.repo, args.terms)
    db = _db()
    try:
        payload = search_documents(
            db=db,
            repo_root=repo_root,
            query=query,
            doc_type=args.doc_type,
            limit=args.limit,
        )
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        print(_format_documents(payload))
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    repo_root, query = _resolve_repo_and_query(args.repo, args.terms)
    db = _db()
    try:
        payload = build_context(
            db=db,
            repo_root=repo_root,
            query=query,
            target_path=args.path,
            agent=args.agent,
            branch=args.branch,
            session_key=args.session,
            memory_limit=args.memory_limit,
            doc_limit=args.doc_limit,
        )
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        print(_format_context(payload))
    return 0


def _cmd_mcp(_: argparse.Namespace) -> int:
    run_stdio_server()
    return 0


def _cmd_auto_init(args: argparse.Namespace) -> int:
    import logging

    repo_root = _resolve_repo_or_raise(args.repo, fallback_to_cwd=True)
    marker = config.repo_state_dir(repo_root) / "project.yaml"

    log_dir = config.global_state_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "auto-init.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )

    if marker.exists():
        return 0
    if not repo_root.is_dir():
        logging.warning("not a directory, skipping: %s", repo_root)
        return 0

    try:
        result = sync_project(
            repo_root=repo_root,
            seed_claude=True,
            include_subagents=True,
            since_days=180,
        )
        logging.info(
            "auto-initialized %s (project_id=%d, docs=%d, rules=%d, sessions=%d)",
            repo_root,
            result.project_id,
            result.docs_indexed,
            result.rules_indexed,
            result.import_result.sessions_imported,
        )
    except Exception:
        logging.exception("auto-init failed for %s", repo_root)
    return 0


def _cmd_setup_hooks(_: argparse.Namespace) -> int:
    import stat

    bin_dir = config.global_state_dir() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "engram-auto-init.sh"
    script.write_text(AUTOINIT_SHELL_SCRIPT, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    script = script.resolve()

    settings_path = config.home_dir() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{settings_path} is not valid JSON: {exc.msg}") from exc
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{settings_path} has a non-object 'hooks' value")
    session_start = hooks.setdefault("SessionStart", [])
    if not isinstance(session_start, list):
        raise ValueError(f"{settings_path} has a non-list hooks.SessionStart value")
    hook_command = f"bash {script}"
    if not any(entry.get("command") == hook_command for entry in session_start if isinstance(entry, dict)):
        session_start.append({"matcher": "", "command": hook_command})

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(settings_path.parent),
        delete=False,
    ) as tmp:
        tmp.write(json.dumps(settings, indent=2) + "\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(settings_path)
    print(f"Wrote hook script: {script}")
    print(f"Updated settings:  {settings_path}")
    return 0


class _ServeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({"ok": True, "service": "engram", "version": __version__})
            return
        if self.path.startswith("/doctor"):
            checks = run_doctor()
            self._send_json(
                {
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
            )
            return
        self._send_json({"ok": False, "error": "not_found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _cmd_serve(args: argparse.Namespace) -> int:
    host, port_str = args.listen.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port_str)), _ServeHandler)
    print(f"Serving on http://{args.listen}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engram")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate local runtime requirements")
    doctor.add_argument("--repo", help="Optional repo path to validate")
    doctor.set_defaults(func=_cmd_doctor)

    init_cmd = subparsers.add_parser("init", help="Bootstrap a repo for engram")
    init_cmd.add_argument("repo", nargs="?", help="Repository root path (default: current repo or cwd)")
    init_cmd.add_argument("--seed-claude", action="store_true", help="Import matching Claude chats")
    init_cmd.add_argument("--include-subagents", action="store_true", help="Include Claude subagent sessions")
    init_cmd.add_argument("--since", default="180d", help="Import Claude sessions newer than this window, e.g. 90d")
    init_cmd.set_defaults(func=_cmd_init)

    sync_cmd = subparsers.add_parser("sync", help="Refresh repo docs, rules, summaries, and optional Claude imports")
    sync_cmd.add_argument("repo", nargs="?", help="Repository root path (default: current repo or cwd)")
    sync_cmd.add_argument("--skip-claude", action="store_true", help="Skip Claude archive import during sync")
    sync_cmd.add_argument("--include-subagents", action="store_true", help="Include Claude subagent sessions")
    sync_cmd.add_argument("--since", default="180d", help="Import Claude sessions newer than this window, e.g. 90d")
    sync_cmd.set_defaults(func=_cmd_sync)

    project = subparsers.add_parser("project", help="Inspect initialized projects")
    project_subparsers = project.add_subparsers(dest="project_command", required=True)
    project_show = project_subparsers.add_parser("show", help="Show project stats and summaries")
    project_show.add_argument("repo", nargs="?", help="Repository root path (default: current repo)")
    project_show.add_argument("--json", action="store_true", help="Emit JSON")
    project_show.set_defaults(func=_cmd_project_show)

    rules = subparsers.add_parser("rules", help="Inspect scoped rules")
    rules_subparsers = rules.add_subparsers(dest="rules_command", required=True)
    rules_show = rules_subparsers.add_parser("show", help="Show applicable rules")
    rules_show.add_argument("repo", nargs="?", help="Repository root path (default: current repo)")
    rules_show.add_argument("--path", help="Optional repo-relative or absolute target path")
    rules_show.add_argument("--agent", help="Optional agent scope key")
    rules_show.add_argument("--branch", help="Optional branch scope key")
    rules_show.add_argument("--session", help="Optional session scope key")
    rules_show.add_argument("--json", action="store_true", help="Emit JSON")
    rules_show.set_defaults(func=_cmd_rules_show)

    memory = subparsers.add_parser("memory", help="Inspect distilled memory")
    memory_subparsers = memory.add_subparsers(dest="memory_command", required=True)
    memory_search = memory_subparsers.add_parser("search", help="Search project memory")
    memory_search.add_argument("terms", nargs="+", help="Query terms, optionally prefixed with a repo path")
    memory_search.add_argument("--repo", help="Optional repository root override")
    memory_search.add_argument("--kind", help="Optional memory kind filter")
    memory_search.add_argument("--limit", type=int, default=10, help="Maximum results")
    memory_search.add_argument("--json", action="store_true", help="Emit JSON")
    memory_search.set_defaults(func=_cmd_memory_search)

    memory_list = memory_subparsers.add_parser("list", help="List stored project memory")
    memory_list.add_argument("--repo", help="Optional repository root override")
    memory_list.add_argument("--kind", help="Optional memory kind filter")
    memory_list.add_argument("--json", action="store_true", help="Emit JSON")
    memory_list.set_defaults(func=_cmd_memory_list)

    memory_store = memory_subparsers.add_parser("store", help="Store a memory item for a project")
    memory_store.add_argument("kind", help="Memory kind, e.g. note, preference, lesson")
    memory_store.add_argument("title", help="Short memory title")
    memory_store.add_argument("body", help="Memory body")
    memory_store.add_argument("--repo", help="Optional repository root override")
    memory_store.add_argument("--source-context", help="Optional source context or excerpt")
    memory_store.add_argument("--json", action="store_true", help="Emit JSON")
    memory_store.set_defaults(func=_cmd_memory_store)

    memory_delete = memory_subparsers.add_parser("delete", help="Delete a stored memory item by ID")
    memory_delete.add_argument("memory_id", type=int, help="Memory item ID")
    memory_delete.add_argument("--repo", help="Optional repository root override")
    memory_delete.add_argument("--json", action="store_true", help="Emit JSON")
    memory_delete.set_defaults(func=_cmd_memory_delete)

    docs = subparsers.add_parser("docs", help="Search indexed repository documents")
    docs_subparsers = docs.add_subparsers(dest="docs_command", required=True)
    docs_search = docs_subparsers.add_parser("search", help="Search indexed documents")
    docs_search.add_argument("terms", nargs="+", help="Query terms, optionally prefixed with a repo path")
    docs_search.add_argument("--repo", help="Optional repository root override")
    docs_search.add_argument("--type", dest="doc_type", help="Optional document type filter")
    docs_search.add_argument("--limit", type=int, default=10, help="Maximum results")
    docs_search.add_argument("--json", action="store_true", help="Emit JSON")
    docs_search.set_defaults(func=_cmd_docs_search)

    context = subparsers.add_parser("context", help="Assemble project context for a coding query")
    context.add_argument("terms", nargs="+", help="Query terms, optionally prefixed with a repo path")
    context.add_argument("--repo", help="Optional repository root override")
    context.add_argument("--path", help="Optional repo-relative or absolute target path")
    context.add_argument("--agent", help="Optional agent scope key")
    context.add_argument("--branch", help="Optional branch scope key")
    context.add_argument("--session", help="Optional session scope key")
    context.add_argument("--memory-limit", type=int, default=5, help="Maximum memory items")
    context.add_argument("--doc-limit", type=int, default=5, help="Maximum document hits")
    context.add_argument("--json", action="store_true", help="Emit JSON")
    context.set_defaults(func=_cmd_context)

    mcp = subparsers.add_parser("mcp", help="Run the MCP stdio server")
    mcp.set_defaults(func=_cmd_mcp)

    auto_init = subparsers.add_parser("auto-init", help="Idempotent init for hooks and first-run setup")
    auto_init.add_argument("repo", nargs="?", help="Repository root path (default: current repo or cwd)")
    auto_init.set_defaults(func=_cmd_auto_init)

    setup_hooks = subparsers.add_parser("setup-hooks", help="Install Claude session-start auto-init hooks")
    setup_hooks.set_defaults(func=_cmd_setup_hooks)

    serve = subparsers.add_parser("serve", help="Run the local HTTP service skeleton")
    serve.add_argument("--listen", default="127.0.0.1:7411", help="Host:port listen address")
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ProjectNotInitializedError as exc:
        print(_uninitialized_message(exc.repo_root), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
