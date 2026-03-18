from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from engram import config
from engram.db import Database
from engram.errors import ProjectNotInitializedError
from engram.rules import load_applicable_rules, scope_priority


def _repo_root(repo_root: Path) -> Path:
    return repo_root.expanduser().resolve()


def _load_project_or_raise(db: Database, repo_root: Path) -> sqlite3.Row:
    resolved_root = _repo_root(repo_root)
    row = db.get_project(resolved_root)
    if row is None:
        raise ProjectNotInitializedError(str(resolved_root))
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
            "last_synced_at": project["updated_at"],
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


def list_memory(
    db: Database,
    repo_root: Path,
    kind: str | None = None,
) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    rows = db.list_memory_items(int(project["id"]), kind=kind)
    return {
        "project_id": int(project["id"]),
        "repo_root": str(repo_root),
        "kind": kind,
        "results": [_memory_row_to_dict(db, row) for row in rows],
    }


def store_memory(
    db: Database,
    repo_root: Path,
    kind: str,
    title: str,
    body: str,
    source_context: str | None = None,
) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    project_id = int(project["id"])
    scope_id = db.ensure_scope(
        project_id=project_id,
        scope_type="repo",
        scope_key=str(repo_root),
        priority=scope_priority("repo"),
    )

    normalized_title = " ".join(title.split()).strip()
    normalized_body = body.strip()
    normalized_kind = kind.strip()
    if not normalized_kind:
        raise ValueError("kind is required")
    if not normalized_title:
        raise ValueError("title is required")
    if not normalized_body:
        raise ValueError("body is required")

    source_key = hashlib.sha1(
        f"{normalized_kind}\n{normalized_title}\n{normalized_body}".encode("utf-8")
    ).hexdigest()
    memory_id = db.upsert_memory_item(
        project_id=project_id,
        scope_id=scope_id,
        kind=normalized_kind,
        title=normalized_title[:80],
        body=normalized_body[:2000],
        source_key=f"manual:{source_key}",
        confidence=0.8,
        importance=0.7,
        stability=0.6,
    )
    db.replace_memory_provenance(
        memory_id,
        [(None, None, None, source_context[:500])] if source_context else [],
    )
    row = db.get_memory_item(project_id, memory_id)
    if row is None:
        raise ValueError(f"failed to load stored memory {memory_id}")
    return {
        "project_id": project_id,
        "repo_root": str(repo_root),
        "stored": True,
        "memory": _memory_row_to_dict(db, row),
    }


def delete_memory(
    db: Database,
    repo_root: Path,
    memory_id: int,
) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    deleted = db.delete_memory_item(int(project["id"]), memory_id)
    return {
        "project_id": int(project["id"]),
        "repo_root": str(repo_root),
        "memory_id": memory_id,
        "deleted": deleted,
    }


def search_documents(
    db: Database,
    repo_root: Path,
    query: str,
    doc_type: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    repo_root = _repo_root(repo_root)
    project = _load_project_or_raise(db, repo_root)
    rows = db.search_documents(
        project_id=int(project["id"]),
        query=_safe_fts_query(query),
        doc_type=doc_type,
        limit=limit,
    )
    return {
        "project_id": int(project["id"]),
        "repo_root": str(repo_root),
        "query": query,
        "doc_type": doc_type,
        "results": [_document_row_to_dict(row) for row in rows],
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
