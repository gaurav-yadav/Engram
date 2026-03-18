from __future__ import annotations

import shutil
from pathlib import Path

from engram import config
from engram.models import DoctorCheck


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".engram_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def run(repo: Path | None = None) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    checks.append(
        DoctorCheck(
            name="git",
            ok=shutil.which("git") is not None,
            detail="git is available on PATH" if shutil.which("git") else "git not found on PATH",
        ),
    )
    checks.append(
        DoctorCheck(
            name="rg",
            ok=shutil.which("rg") is not None,
            detail="rg is available on PATH" if shutil.which("rg") else "rg not found on PATH",
        ),
    )

    global_state = config.global_state_dir()
    global_state_writable = _is_writable_dir(global_state)
    checks.append(
        DoctorCheck(
            name="global_state",
            ok=global_state_writable,
            detail=f"{global_state} is writable" if global_state_writable else f"{global_state} is not writable",
        ),
    )

    db_parent = config.db_path().parent
    db_parent_writable = _is_writable_dir(db_parent)
    checks.append(
        DoctorCheck(
            name="sqlite_path",
            ok=db_parent_writable,
            detail=f"{db_parent} can host the SQLite database"
            if db_parent_writable
            else f"{db_parent} is not writable",
        ),
    )

    claude_dir = config.claude_projects_dir()
    checks.append(
        DoctorCheck(
            name="claude_history",
            ok=claude_dir.exists(),
            detail=f"Claude history found at {claude_dir}" if claude_dir.exists() else f"Claude history missing at {claude_dir}",
            required=False,
        ),
    )

    if repo is not None:
        marker = config.repo_state_dir(repo) / "project.yaml"
        checks.append(
            DoctorCheck(
                name="repo_exists",
                ok=repo.exists(),
                detail=f"{repo} exists" if repo.exists() else f"{repo} does not exist",
            ),
        )
        checks.append(
            DoctorCheck(
                name="repo_access",
                ok=repo.exists() and repo.is_dir(),
                detail=f"{repo} is a readable directory"
                if repo.exists() and repo.is_dir()
                else f"{repo} is not a readable directory",
            ),
        )
        checks.append(
            DoctorCheck(
                name="project_initialized",
                ok=marker.exists(),
                detail=f"Engram project marker found at {marker}"
                if marker.exists()
                else f"Engram project marker missing at {marker}; run `engram auto-init` or `engram init`",
                required=False,
            ),
        )
    return checks


def format_checks(checks: list[DoctorCheck]) -> str:
    lines: list[str] = []
    for check in checks:
        marker = "PASS" if check.ok else "FAIL" if check.required else "WARN"
        suffix = "" if check.required else " (optional)"
        lines.append(f"[{marker}] {check.name}{suffix}: {check.detail}")
    return "\n".join(lines)


def all_required_ok(checks: list[DoctorCheck]) -> bool:
    return all(check.ok for check in checks if check.required)
