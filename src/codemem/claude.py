from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codemem import config
from codemem.db import Database
from codemem.models import ClaudeEvent, ClaudeSession, ImportResult


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _extract_blocks(record: dict[str, object]) -> list[tuple[str, str | None, str, str | None]]:
    record_type = str(record.get("type") or "")
    role = None
    message = record.get("message")
    if isinstance(message, dict):
        role = message.get("role") if isinstance(message.get("role"), str) else None
        content = message.get("content")
        if isinstance(content, str):
            return [(record_type or f"{role}_text", None, content, None)]
        if isinstance(content, list):
            blocks: list[tuple[str, str | None, str, str | None]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "block")
                if block_type == "text":
                    blocks.append((f"{role}_text", None, _stringify(block.get("text")), json.dumps(block)))
                elif block_type == "thinking":
                    blocks.append((f"{role}_thinking", None, _stringify(block.get("thinking")), json.dumps(block)))
                elif block_type == "tool_use":
                    tool_name = _stringify(block.get("name")) or None
                    blocks.append(
                        (
                            f"{role}_tool_use",
                            tool_name,
                            _stringify(block.get("input")),
                            json.dumps(block),
                        ),
                    )
                elif block_type == "tool_result":
                    blocks.append((f"{role}_tool_result", None, _stringify(block.get("content")), json.dumps(block)))
                else:
                    blocks.append((f"{role}_{block_type}", None, _stringify(block), json.dumps(block)))
            return blocks
    if record_type == "queue-operation":
        return [(f"queue_{_stringify(record.get('operation'))}", None, _stringify(record.get("content")), json.dumps(record))]
    return [(record_type or "event", None, _stringify(record), json.dumps(record))]


def parse_session_file(path: Path) -> ClaudeSession:
    digest = hashlib.sha256()
    events: list[ClaudeEvent] = []
    cwd_values: list[str] = []
    branch_values: list[str] = []
    session_id = path.stem
    agent_id: str | None = None
    is_sidechain = False
    started_at: str | None = None
    ended_at: str | None = None

    with path.open("rb") as raw_file:
        for raw_line in raw_file:
            digest.update(raw_line)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            session_id = _stringify(record.get("sessionId")) or session_id
            candidate_agent = _stringify(record.get("agentId"))
            if candidate_agent:
                agent_id = candidate_agent
            is_sidechain = is_sidechain or bool(record.get("isSidechain"))

            cwd = _stringify(record.get("cwd"))
            if cwd:
                cwd_values.append(cwd)
            git_branch = _stringify(record.get("gitBranch"))
            if git_branch:
                branch_values.append(git_branch)

            timestamp = _stringify(record.get("timestamp")) or None
            if timestamp and started_at is None:
                started_at = timestamp
            if timestamp:
                ended_at = timestamp

            parent_uuid = _stringify(record.get("parentUuid")) or None
            uuid = _stringify(record.get("uuid")) or None
            role = None
            if isinstance(record.get("message"), dict):
                value = record["message"].get("role")
                if isinstance(value, str):
                    role = value

            for event_type, tool_name, content_text, content_json in _extract_blocks(record):
                events.append(
                    ClaudeEvent(
                        event_type=event_type,
                        role=role,
                        tool_name=tool_name,
                        content_text=content_text,
                        content_json=content_json,
                        timestamp=timestamp,
                        parent_uuid=parent_uuid,
                        uuid=uuid,
                    ),
                )

    cwd = Counter(cwd_values).most_common(1)[0][0] if cwd_values else None
    git_branch = Counter(branch_values).most_common(1)[0][0] if branch_values else None
    return ClaudeSession(
        source_path=path,
        file_hash=digest.hexdigest(),
        session_id=session_id,
        agent_id=agent_id,
        cwd=cwd,
        git_branch=git_branch,
        is_sidechain=is_sidechain,
        started_at=started_at,
        ended_at=ended_at,
        events=events,
    )


def _session_matches_repo(session: ClaudeSession, repo_root: Path, include_subagents: bool) -> bool:
    if session.is_sidechain and not include_subagents:
        return False
    if not session.cwd:
        return False
    try:
        Path(session.cwd).resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    return True


def _session_is_recent_enough(session: ClaudeSession, since_days: int | None) -> bool:
    if since_days is None:
        return True
    started = _parse_timestamp(session.started_at)
    if started is None:
        return True
    threshold = datetime.now(tz=timezone.utc) - timedelta(days=since_days)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started >= threshold


def _extract_bash_commands(session: ClaudeSession) -> list[str]:
    commands: list[str] = []
    for event in session.events:
        if event.event_type != "assistant_tool_use" or (event.tool_name or "").lower() != "bash":
            continue
        if not event.content_json:
            continue
        try:
            payload = json.loads(event.content_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        input_data = payload.get("input")
        if isinstance(input_data, dict):
            command = input_data.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command.strip())
    return commands


NOISY_PREFIXES = (
    "cat ",
    "sed ",
    "head ",
    "tail ",
    "ls ",
    "pwd",
    "echo ",
    "grep ",
    "rg ",
    "find ",
    "stat ",
    "mkdir ",
    "touch ",
    "rm ",
    "/bin/rm ",
    "mv ",
    "cp ",
    "awk ",
    "perl ",
    "git status",
    "git diff",
    "git log",
    "git show",
)

USEFUL_COMMAND_HINTS = (
    "pytest",
    "unittest",
    "ruff",
    "mypy",
    "uv run",
    "uvx ",
    "python -m",
    "python3 -m",
    "npm ",
    "pnpm ",
    "yarn ",
    "bun ",
    "node ",
    "tsx ",
    "tsc",
    "make ",
    "docker ",
    "docker compose",
    "cargo ",
    "go test",
    "go build",
    "streamlit ",
)

PREFERENCE_HINTS = (
    "i prefer",
    "prefer ",
    "local only",
    "local-first",
    "per repo",
    "global rules",
    "global rule",
    "do not ",
    "don't ",
    "avoid ",
    "always ",
    "never ",
    "must ",
    "should ",
    "i want",
    "we can have",
)


def _normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def _normalize_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    return cleaned.strip(" .!?,;:-")


def _is_useful_command(command: str, count: int) -> bool:
    normalized = _normalize_command(command)
    lowered = normalized.lower()
    if not normalized or len(normalized) > 300:
        return False
    if "<<" in normalized or " cat > " in f" {normalized}" or normalized.startswith("cat > "):
        return False
    if any(lowered.startswith(prefix) for prefix in NOISY_PREFIXES):
        return False
    if count >= 2:
        return True
    return any(hint in lowered for hint in USEFUL_COMMAND_HINTS)


def _promote_command_memories(db: Database, project_id: int, repo_scope_id: int) -> tuple[int, list[str]]:
    rows = db.conn.execute(
        """
        SELECT ae.id AS archive_event_id, ae.archive_session_id, ae.content_json
        FROM archive_events ae
        JOIN archive_sessions s ON s.id = ae.archive_session_id
        WHERE s.project_id = ?
          AND ae.event_type = 'assistant_tool_use'
          AND LOWER(ae.tool_name) = 'bash'
        """,
        (project_id,),
    ).fetchall()

    counter: Counter[str] = Counter()
    provenance_by_command: dict[str, list[tuple[int, int, str | None]]] = {}
    for row in rows:
        raw = row["content_json"]
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        input_data = payload.get("input")
        if not isinstance(input_data, dict):
            continue
        command = input_data.get("command")
        if isinstance(command, str) and command.strip():
            normalized = _normalize_command(command)
            counter[normalized] += 1
            provenance_by_command.setdefault(normalized, [])
            if len(provenance_by_command[normalized]) < 3:
                provenance_by_command[normalized].append(
                    (int(row["archive_session_id"]), int(row["archive_event_id"]), normalized[:240]),
                )

    promoted = [
        command
        for command, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        if _is_useful_command(command, count)
    ][:50]

    db.delete_memory_items(project_id, repo_scope_id, "command")
    for command in promoted:
        source_key = hashlib.sha1(command.encode("utf-8")).hexdigest()
        title = command[:80]
        count = counter[command]
        memory_id = db.upsert_memory_item(
            project_id=project_id,
            scope_id=repo_scope_id,
            kind="command",
            title=title,
            body=f"{command}\n\nOccurrences: {count}",
            source_key=f"cmd:{source_key}",
            confidence=min(0.95, 0.55 + count * 0.08),
            importance=min(0.95, 0.5 + count * 0.05),
            stability=min(0.95, 0.45 + count * 0.08),
        )
        db.replace_memory_provenance(
            memory_id,
            [
                (archive_session_id, archive_event_id, None, excerpt)
                for archive_session_id, archive_event_id, excerpt in provenance_by_command.get(command, [])
            ],
        )
    return len(promoted), promoted


def _preference_candidates(text: str) -> list[str]:
    chunks = re.split(r"[\n\r]+|(?<=[.!?])\s+", text)
    candidates: list[str] = []
    for chunk in chunks:
        candidate = " ".join(chunk.strip().split())
        lowered = candidate.lower()
        if len(candidate) < 18 or len(candidate) > 220:
            continue
        if not any(hint in lowered for hint in PREFERENCE_HINTS):
            continue
        candidates.append(candidate)
    return candidates


def _is_useful_preference(text: str, count: int) -> bool:
    lowered = text.lower()
    if "?" in text:
        return False
    if count >= 2:
        return True
    return any(
        hint in lowered
        for hint in (
            "i prefer",
            "local only",
            "local-first",
            "per repo",
            "global rules",
            "do not ",
            "don't ",
            "avoid ",
            "always ",
            "never ",
            "must ",
        )
    )


def _promote_preference_memories(db: Database, project_id: int, repo_scope_id: int) -> tuple[int, list[str]]:
    rows = db.conn.execute(
        """
        SELECT ae.id AS archive_event_id, ae.archive_session_id, ae.content_text
        FROM archive_events ae
        JOIN archive_sessions s ON s.id = ae.archive_session_id
        WHERE s.project_id = ?
          AND ae.event_type = 'user_text'
          AND TRIM(ae.content_text) <> ''
        """,
        (project_id,),
    ).fetchall()

    counter: Counter[str] = Counter()
    display_by_key: dict[str, str] = {}
    provenance_by_key: dict[str, list[tuple[int, int, str | None]]] = {}
    for row in rows:
        for candidate in _preference_candidates(str(row["content_text"] or "")):
            key = _normalize_text(candidate)
            if not key:
                continue
            counter[key] += 1
            display_by_key.setdefault(key, candidate)
            provenance_by_key.setdefault(key, [])
            if len(provenance_by_key[key]) < 3:
                provenance_by_key[key].append(
                    (int(row["archive_session_id"]), int(row["archive_event_id"]), candidate[:240]),
                )

    promoted_keys = [
        key
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        if _is_useful_preference(display_by_key[key], count)
    ][:30]

    db.delete_memory_items(project_id, repo_scope_id, "preference")
    for key in promoted_keys:
        text = display_by_key[key]
        count = counter[key]
        memory_id = db.upsert_memory_item(
            project_id=project_id,
            scope_id=repo_scope_id,
            kind="preference",
            title=text[:80],
            body=f"{text}\n\nOccurrences: {count}",
            source_key=f"pref:{hashlib.sha1(key.encode('utf-8')).hexdigest()}",
            confidence=min(0.95, 0.58 + count * 0.08),
            importance=min(0.95, 0.6 + count * 0.05),
            stability=min(0.95, 0.6 + count * 0.06),
        )
        db.replace_memory_provenance(
            memory_id,
            [
                (archive_session_id, archive_event_id, None, excerpt)
                for archive_session_id, archive_event_id, excerpt in provenance_by_key.get(key, [])
            ],
        )
    return len(promoted_keys), [display_by_key[key] for key in promoted_keys]


def import_claude_history(
    db: Database,
    project_id: int,
    repo_scope_id: int,
    repo_root: Path,
    include_subagents: bool,
    since_days: int | None,
) -> ImportResult:
    result = ImportResult()
    claude_root = config.claude_projects_dir()
    if not claude_root.exists():
        return result

    for path in sorted(claude_root.rglob("*.jsonl")):
        result.files_seen += 1
        session = parse_session_file(path)
        if not _session_matches_repo(session, repo_root, include_subagents):
            result.sessions_skipped += 1
            continue
        if not _session_is_recent_enough(session, since_days):
            result.sessions_skipped += 1
            continue
        if db.archive_session_exists(str(path), session.file_hash):
            result.sessions_skipped += 1
            continue

        archive_session_id = db.insert_archive_session(
            project_id=project_id,
            session_id=session.session_id,
            agent_id=session.agent_id,
            cwd=session.cwd,
            git_branch=session.git_branch,
            is_sidechain=session.is_sidechain,
            started_at=session.started_at,
            ended_at=session.ended_at,
            source_path=str(path),
            file_hash=session.file_hash,
        )
        db.insert_archive_events(
            archive_session_id,
            [
                (
                    index,
                    event.event_type,
                    event.role,
                    event.tool_name,
                    event.content_text,
                    event.content_json,
                    event.timestamp,
                    event.parent_uuid,
                    event.uuid,
                )
                for index, event in enumerate(session.events)
            ],
        )
        result.sessions_imported += 1
        result.events_imported += len(session.events)

    result.command_memories_added, result.commands = _promote_command_memories(
        db,
        project_id,
        repo_scope_id,
    )
    result.preference_memories_added, result.preferences = _promote_preference_memories(
        db,
        project_id,
        repo_scope_id,
    )
    return result
