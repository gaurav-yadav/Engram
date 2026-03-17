from __future__ import annotations

import json
from pathlib import Path

from engram.models import DetectedDoc, ImportResult


def write_summaries(
    repo_root: Path,
    detected_docs: list[DetectedDoc],
    import_result: ImportResult,
    project_stats: dict[str, int] | None = None,
) -> list[Path]:
    summaries_dir = repo_root / ".engram" / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    project_summary = summaries_dir / "project-summary.md"
    commands_summary = summaries_dir / "commands.md"
    preferences_summary = summaries_dir / "preferences.md"
    imports_manifest = repo_root / ".engram" / "imports" / "claude-seed-manifest.json"
    imports_manifest.parent.mkdir(parents=True, exist_ok=True)

    discovered: list[str] = []
    for doc in detected_docs:
        try:
            display_path = doc.path.relative_to(repo_root)
        except ValueError:
            display_path = doc.path
        discovered.append(f"- `{display_path}` ({doc.doc_type}, {doc.source_kind})")
    project_summary.write_text(
        "\n".join(
            [
                "# Project Summary",
                "",
                f"- Repo: `{repo_root}`",
                f"- Indexed documents: {len(detected_docs)}",
                f"- Claude sessions in archive: {(project_stats or {}).get('archive_sessions_count', import_result.sessions_imported)}",
                f"- Archive events in archive: {(project_stats or {}).get('archive_events_count', import_result.events_imported)}",
                f"- Last init imported sessions: {import_result.sessions_imported}",
                f"- Last init imported events: {import_result.events_imported}",
                f"- Command memories promoted: {import_result.command_memories_added}",
                f"- Preference memories promoted: {import_result.preference_memories_added}",
                "",
                "## Discovered Documents",
                *(discovered or ["- None"]),
                "",
            ],
        ),
        encoding="utf-8",
    )

    command_lines = ["# Commands", ""]
    if import_result.commands:
        for command in import_result.commands:
            command_lines.append("```bash")
            command_lines.append(command)
            command_lines.append("```")
            command_lines.append("")
    else:
        command_lines.append("No imported Claude Bash commands yet.")
        command_lines.append("")
    commands_summary.write_text("\n".join(command_lines), encoding="utf-8")

    preference_lines = ["# Preferences", ""]
    if import_result.preferences:
        for preference in import_result.preferences:
            preference_lines.append(f"- {preference}")
    else:
        preference_lines.append("No imported user preference memories yet.")
    preference_lines.append("")
    preferences_summary.write_text("\n".join(preference_lines), encoding="utf-8")

    imports_manifest.write_text(
        json.dumps(
            {
                "files_seen": import_result.files_seen,
                "sessions_imported": import_result.sessions_imported,
                "sessions_skipped": import_result.sessions_skipped,
                "events_imported": import_result.events_imported,
                "command_memories_added": import_result.command_memories_added,
                "preference_memories_added": import_result.preference_memories_added,
                "archive_sessions_total": (project_stats or {}).get("archive_sessions_count", import_result.sessions_imported),
                "archive_events_total": (project_stats or {}).get("archive_events_count", import_result.events_imported),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return [project_summary, commands_summary, preferences_summary, imports_manifest]
