from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from engram import config
from engram.db import Database
from engram.rules import load_applicable_rules


def _repo_root(repo_root: Path) -> Path:
    return repo_root.expanduser().resolve()


def _load_project_or_raise(db: Database, repo_root: Path) -> sqlite3.Row:
    row = db.get_project(_repo_root(repo_root))
    if row is None:
        raise ValueError(f"project is not initialized: {repo_root}")
    return row


def _safe_fts_query(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_]+", query)
    if not tokens:
        raise ValueError("query must contain at least one alphanumeric token")
    return " ".join(f'"{token}"' for token in tokens)


def _resolve_target_path(repo_root: Path, raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _read_summary(repo_root: Path, name: str) -> str | None:
    path = config.repo_state_dir(repo_root) / "summaries" / name
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _rule_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "scope_type": row["scope_type"],
        "scope_key": row["scope_key"],
        "title": row["title"],
        "body": row["body"],
        "source_path": row["source_path"],
        "source_kind": row["source_kind"],
    }


def _memory_row_to_dict(db: Database, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "scope_type": row["scope_type"],
        "scope_key": row["scope_key"],
        "confidence": float(row["confidence"]),
        "importance": float(row["importance"]),
        "stability": float(row["stability"]),
        "source_key": row["source_key"],
        "provenance": [
            {
                "archive_session_id": provenance["archive_session_id"],
                "archive_event_id": provenance["archive_event_id"],
                "document_id": provenance["document_id"],
                "source_excerpt": provenance["source_excerpt"],
            }
            for provenance in db.get_memory_provenance(int(row["id"]))
        ],
    }


def _document_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "doc_type": row["doc_type"],
        "title": row["title"],
        "path": row["path"],
        "scope_type": row["scope_type"],
        "scope_key": row["scope_key"],
        "body": row["body"],
    }


def get_project_snapshot(db: Database, repo_root: Path) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    stats = db.project_stats(int(project["id"]))
    return {
        "project": {
            "id": int(project["id"]),
            "repo_path": project["repo_path"],
            "repo_name": project["repo_name"],
            "default_branch": project["default_branch"],
            "language_summary": project["language_summary"],
            "created_at": project["created_at"],
            "updated_at": project["updated_at"],
        },
        "stats": stats,
        "summaries": {
            "project": _read_summary(repo_root, "project-summary.md"),
            "commands": _read_summary(repo_root, "commands.md"),
            "preferences": _read_summary(repo_root, "preferences.md"),
        },
    }


def get_applicable_rules(
    db: Database,
    repo_root: Path,
    target_path: str | None = None,
    agent: str | None = None,
    branch: str | None = None,
    session_key: str | None = None,
) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    resolved_path = _resolve_target_path(repo_root, target_path)
    rows = load_applicable_rules(
        db.conn,
        int(project["id"]),
        repo_root=repo_root,
        target_path=resolved_path,
        agent=agent,
        branch=branch,
        session_key=session_key,
    )
    return {
        "project_id": int(project["id"]),
        "repo_root": str(repo_root),
        "target_path": str(resolved_path) if resolved_path else None,
        "agent": agent,
        "branch": branch,
        "session_key": session_key,
        "rules": [_rule_row_to_dict(row) for row in rows],
    }


def search_memory(
    db: Database,
    repo_root: Path,
    query: str,
    kind: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    rows = db.search_memory(
        project_id=int(project["id"]),
        query=_safe_fts_query(query),
        kind=kind,
        limit=limit,
    )
    return {
        "project_id": int(project["id"]),
        "repo_root": str(repo_root),
        "query": query,
        "kind": kind,
        "results": [_memory_row_to_dict(db, row) for row in rows],
    }


def build_context(
    db: Database,
    repo_root: Path,
    query: str,
    target_path: str | None = None,
    agent: str | None = None,
    branch: str | None = None,
    session_key: str | None = None,
    memory_limit: int = 5,
    doc_limit: int = 5,
) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    rules_payload = get_applicable_rules(
        db=db,
        repo_root=repo_root,
        target_path=target_path,
        agent=agent,
        branch=branch,
        session_key=session_key,
    )
    memory_rows = db.search_memory(
        project_id=int(project["id"]),
        query=_safe_fts_query(query),
        limit=memory_limit,
    )
    document_rows = db.search_documents(
        project_id=int(project["id"]),
        query=_safe_fts_query(query),
        limit=doc_limit,
    )
    return {
        "project_id": int(project["id"]),
        "repo_root": str(repo_root),
        "query": query,
        "summary": _read_summary(repo_root, "project-summary.md"),
        "rules": rules_payload["rules"],
        "memory": [_memory_row_to_dict(db, row) for row in memory_rows],
        "documents": [_document_row_to_dict(row) for row in document_rows],
    }
