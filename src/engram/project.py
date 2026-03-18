from __future__ import annotations

from pathlib import Path

from engram import config
from engram.claude import import_claude_history
from engram.db import Database
from engram.models import DetectedDoc, ImportResult, InitResult, SyncResult
from engram.repoindex import ensure_repo_layout, hash_text, scan_global_rules, scan_repo
from engram.rules import scope_priority
from engram.summary import write_summaries


def _scope_key_for_repo(repo_root: Path, doc: DetectedDoc) -> str:
    if doc.scope_type == "repo":
        return str(repo_root)
    return doc.scope_key


def _refresh_project_state(
    repo_root: Path,
    seed_claude: bool,
    include_subagents: bool,
    since_days: int | None,
) -> SyncResult:
    repo_root = repo_root.resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise ValueError(f"{repo_root} is not a readable repository directory")

    config.ensure_default_global_config()
    ensure_repo_layout(repo_root)

    with Database(config.db_path()) as db:
        db.migrate()
        project_id = db.get_or_create_project(repo_root)
        global_scope_id = db.ensure_scope(
            project_id=project_id,
            scope_type="global",
            scope_key="global",
            priority=scope_priority("global"),
        )
        repo_scope_id = db.ensure_scope(
            project_id=project_id,
            scope_type="repo",
            scope_key=str(repo_root),
            priority=scope_priority("repo"),
        )

        docs = scan_global_rules() + scan_repo(repo_root)
        rules_indexed = 0
        for doc in docs:
            default_parent = None
            if doc.scope_type == "repo":
                default_parent = global_scope_id
            elif doc.scope_type in {"global", "global_agent"}:
                default_parent = global_scope_id if doc.scope_type == "global_agent" else None
            elif doc.scope_type != "global":
                default_parent = repo_scope_id
            scope_id = db.ensure_scope(
                project_id=project_id,
                scope_type=doc.scope_type,
                scope_key=_scope_key_for_repo(repo_root, doc),
                parent_scope_id=default_parent,
                priority=scope_priority(doc.scope_type),
            )
            db.upsert_document(
                project_id=project_id,
                scope_id=scope_id,
                doc_type=doc.doc_type,
                path=str(doc.path),
                title=doc.title,
                body=doc.body,
                metadata=doc.metadata,
            )
            if doc.doc_type == "rule":
                db.upsert_rule(
                    scope_id=scope_id,
                    source_path=str(doc.path),
                    source_kind=doc.source_kind,
                    title=doc.title,
                    body=doc.body,
                    body_hash=hash_text(doc.body),
                )
                rules_indexed += 1

        import_result = ImportResult()
        if seed_claude:
            import_result = import_claude_history(
                db=db,
                project_id=project_id,
                repo_scope_id=repo_scope_id,
                repo_root=repo_root,
                include_subagents=include_subagents,
                since_days=since_days,
            )

        db.touch_project(project_id)
        project_stats = db.project_stats(project_id)
        summaries = write_summaries(repo_root, docs, import_result, project_stats=project_stats)

    return SyncResult(
        repo_root=repo_root,
        project_id=project_id,
        docs_indexed=len(docs),
        rules_indexed=rules_indexed,
        import_result=import_result,
        summaries_written=summaries,
    )


def initialize_project(
    repo_root: Path,
    seed_claude: bool,
    include_subagents: bool,
    since_days: int | None,
) -> InitResult:
    result = _refresh_project_state(repo_root, seed_claude, include_subagents, since_days)
    return InitResult(
        repo_root=result.repo_root,
        project_id=result.project_id,
        docs_indexed=result.docs_indexed,
        rules_indexed=result.rules_indexed,
        import_result=result.import_result,
        summaries_written=result.summaries_written,
    )


def sync_project(
    repo_root: Path,
    seed_claude: bool = True,
    include_subagents: bool = False,
    since_days: int | None = 180,
) -> SyncResult:
    return _refresh_project_state(repo_root, seed_claude, include_subagents, since_days)
