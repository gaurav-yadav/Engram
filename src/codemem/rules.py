from __future__ import annotations

import sqlite3
from pathlib import Path


SCOPE_ORDER = {
    "global": 10,
    "repo": 20,
    "path": 30,
    "global_agent": 35,
    "agent": 40,
    "branch": 50,
    "session": 60,
}


def scope_priority(scope_type: str) -> int:
    return SCOPE_ORDER.get(scope_type, 0)


def resolve_path_scope(repo_root: Path, target_path: Path | None) -> str | None:
    if target_path is None:
        return None
    try:
        relative = target_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    if str(relative) == ".":
        return None
    return relative.as_posix()


def _path_scope_candidates(path_scope: str | None) -> list[str]:
    if not path_scope:
        return []
    parts = path_scope.split("/")
    return ["/".join(parts[: index + 1]) for index in range(len(parts))]


def load_applicable_rules(
    conn: sqlite3.Connection,
    project_id: int,
    repo_root: Path,
    target_path: Path | None = None,
    agent: str | None = None,
    branch: str | None = None,
    session_key: str | None = None,
) -> list[sqlite3.Row]:
    path_scope = resolve_path_scope(repo_root, target_path)
    requested_scopes: list[tuple[str, str]] = [("global", "global"), ("repo", str(repo_root))]
    requested_scopes.extend(("path", candidate) for candidate in _path_scope_candidates(path_scope))
    if agent:
        requested_scopes.append(("global_agent", agent))
        requested_scopes.append(("agent", agent))
    if branch:
        requested_scopes.append(("branch", branch))
    if session_key:
        requested_scopes.append(("session", session_key))
    results: list[sqlite3.Row] = []
    for scope_type, scope_key in requested_scopes:
        rows = conn.execute(
            """
            SELECT r.*, s.scope_type, s.scope_key, s.priority
            FROM rules r
            JOIN scopes s ON s.id = r.scope_id
            WHERE s.project_id = ? AND s.scope_type = ? AND s.scope_key = ? AND r.active = 1
            ORDER BY s.priority ASC, r.source_path ASC
            """,
            (project_id, scope_type, scope_key),
        ).fetchall()
        results.extend(rows)
    return results
