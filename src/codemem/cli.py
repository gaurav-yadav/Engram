from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from codemem import __version__
from codemem.db import Database
from codemem.doctor import all_required_ok, format_checks, run as run_doctor
from codemem.mcp import run_stdio_server
from codemem.project import initialize_project
from codemem.query import build_context, get_applicable_rules, get_project_snapshot, search_memory


def _parse_since(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if value.endswith("d"):
        value = value[:-1]
    return int(value)


def _cmd_doctor(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve() if args.repo else None
    checks = run_doctor(repo)
    print(format_checks(checks))
    return 0 if all_required_ok(checks) else 1


def _cmd_init(args: argparse.Namespace) -> int:
    result = initialize_project(
        repo_root=Path(args.repo),
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


def _db() -> Database:
    from codemem import config

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
            lines.append(f"- [{item['kind']}] {item['title']}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Matching documents:")
    if payload["documents"]:
        for doc in payload["documents"]:
            lines.append(f"- [{doc['doc_type']}] {doc['path']}")
    else:
        lines.append("- None")
    return "\n".join(lines).rstrip()


def _cmd_rules_show(args: argparse.Namespace) -> int:
    db = _db()
    try:
        payload = get_applicable_rules(
            db=db,
            repo_root=Path(args.repo),
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
    db = _db()
    try:
        payload = search_memory(
            db=db,
            repo_root=Path(args.repo),
            query=args.query,
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


def _cmd_project_show(args: argparse.Namespace) -> int:
    db = _db()
    try:
        payload = get_project_snapshot(db=db, repo_root=Path(args.repo))
    finally:
        db.close()
    if args.json:
        _print_json(payload)
    else:
        print(_format_project(payload))
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    db = _db()
    try:
        payload = build_context(
            db=db,
            repo_root=Path(args.repo),
            query=args.query,
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


class _ServeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({"ok": True, "service": "codemem", "version": __version__})
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
    parser = argparse.ArgumentParser(prog="codemem")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate local runtime requirements")
    doctor.add_argument("--repo", help="Optional repo path to validate")
    doctor.set_defaults(func=_cmd_doctor)

    init_cmd = subparsers.add_parser("init", help="Bootstrap a repo for codemem")
    init_cmd.add_argument("repo", help="Repository root path")
    init_cmd.add_argument("--seed-claude", action="store_true", help="Import matching Claude chats")
    init_cmd.add_argument("--include-subagents", action="store_true", help="Include Claude subagent sessions")
    init_cmd.add_argument("--since", default="180d", help="Import Claude sessions newer than this window, e.g. 90d")
    init_cmd.set_defaults(func=_cmd_init)

    project = subparsers.add_parser("project", help="Inspect initialized projects")
    project_subparsers = project.add_subparsers(dest="project_command", required=True)
    project_show = project_subparsers.add_parser("show", help="Show project stats and summaries")
    project_show.add_argument("repo", help="Repository root path")
    project_show.add_argument("--json", action="store_true", help="Emit JSON")
    project_show.set_defaults(func=_cmd_project_show)

    rules = subparsers.add_parser("rules", help="Inspect scoped rules")
    rules_subparsers = rules.add_subparsers(dest="rules_command", required=True)
    rules_show = rules_subparsers.add_parser("show", help="Show applicable rules")
    rules_show.add_argument("repo", help="Repository root path")
    rules_show.add_argument("--path", help="Optional repo-relative or absolute target path")
    rules_show.add_argument("--agent", help="Optional agent scope key")
    rules_show.add_argument("--branch", help="Optional branch scope key")
    rules_show.add_argument("--session", help="Optional session scope key")
    rules_show.add_argument("--json", action="store_true", help="Emit JSON")
    rules_show.set_defaults(func=_cmd_rules_show)

    memory = subparsers.add_parser("memory", help="Inspect distilled memory")
    memory_subparsers = memory.add_subparsers(dest="memory_command", required=True)
    memory_search = memory_subparsers.add_parser("search", help="Search project memory")
    memory_search.add_argument("repo", help="Repository root path")
    memory_search.add_argument("query", help="Search query")
    memory_search.add_argument("--kind", help="Optional memory kind filter")
    memory_search.add_argument("--limit", type=int, default=10, help="Maximum results")
    memory_search.add_argument("--json", action="store_true", help="Emit JSON")
    memory_search.set_defaults(func=_cmd_memory_search)

    context = subparsers.add_parser("context", help="Assemble project context for a coding query")
    context.add_argument("repo", help="Repository root path")
    context.add_argument("query", help="Coding question or task")
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

    serve = subparsers.add_parser("serve", help="Run the local HTTP service skeleton")
    serve.add_argument("--listen", default="127.0.0.1:7411", help="Host:port listen address")
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
