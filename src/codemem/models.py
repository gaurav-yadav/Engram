from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass
class DetectedDoc:
    path: Path
    title: str
    body: str
    doc_type: str
    source_kind: str
    scope_type: str = "repo"
    scope_key: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class ClaudeEvent:
    event_type: str
    role: str | None
    tool_name: str | None
    content_text: str
    content_json: str | None
    timestamp: str | None
    parent_uuid: str | None
    uuid: str | None


@dataclass
class ClaudeSession:
    source_path: Path
    file_hash: str
    session_id: str
    agent_id: str | None
    cwd: str | None
    git_branch: str | None
    is_sidechain: bool
    started_at: str | None
    ended_at: str | None
    events: list[ClaudeEvent]


@dataclass
class ImportResult:
    files_seen: int = 0
    sessions_imported: int = 0
    sessions_skipped: int = 0
    events_imported: int = 0
    command_memories_added: int = 0
    preference_memories_added: int = 0
    commands: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)


@dataclass
class InitResult:
    repo_root: Path
    project_id: int
    docs_indexed: int
    rules_indexed: int
    import_result: ImportResult
    summaries_written: list[Path]
