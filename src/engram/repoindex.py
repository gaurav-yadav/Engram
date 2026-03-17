from __future__ import annotations

import hashlib
from pathlib import Path

from engram import config
from engram.models import DetectedDoc


ROOT_DOC_CANDIDATES = (
    "README",
    "README.md",
    "README.rst",
    "README.txt",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "Makefile",
    "AGENTS.md",
    "CLAUDE.md",
)

IGNORE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}


def ensure_repo_layout(repo_root: Path) -> None:
    state_dir = repo_root / ".engram"
    for relative in (
        "rules",
        "rules/agents",
        "rules/paths",
        "rules/branches",
        "rules/sessions",
        "summaries",
        "imports",
        "cache",
    ):
        (state_dir / relative).mkdir(parents=True, exist_ok=True)

    repo_rule = state_dir / "rules" / "repo.md"
    if not repo_rule.exists():
        repo_rule.write_text(
            "\n".join(
                [
                    "# Repo Rules",
                    "",
                    "<!-- Add persistent repo-specific rules here. -->",
                    "",
                ],
            ),
            encoding="utf-8",
        )

    project_yaml = state_dir / "project.yaml"
    if not project_yaml.exists():
        project_yaml.write_text(
            "\n".join(
                [
                    "project:",
                    f"  name: {repo_root.name}",
                    f"  repo_root: {repo_root}",
                    "seed:",
                    "  claude:",
                    "    enabled: true",
                    "    since_days: 180",
                    "    include_subagents: true",
                    "",
                ],
            ),
            encoding="utf-8",
        )


def _read_text(path: Path, max_bytes: int = 200_000) -> str:
    raw = path.read_bytes()
    return raw[:max_bytes].decode("utf-8", errors="replace")


def _doc_type_for_name(name: str) -> tuple[str, str]:
    if name in {"AGENTS.md", "CLAUDE.md"}:
        return ("rule", "repo_rule")
    if name.startswith("README"):
        return ("readme", "readme")
    if name in {"pyproject.toml", "package.json", "Cargo.toml", "Makefile"}:
        return ("manifest", "manifest")
    return ("document", "document")


def _title_for_doc(path: Path) -> str:
    return path.name


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_repo(repo_root: Path) -> list[DetectedDoc]:
    docs: list[DetectedDoc] = []

    for name in ROOT_DOC_CANDIDATES:
        path = repo_root / name
        if not path.exists() or not path.is_file():
            continue
        doc_type, source_kind = _doc_type_for_name(name)
        body = _read_text(path)
        docs.append(
            DetectedDoc(
                path=path,
                title=_title_for_doc(path),
                body=body,
                doc_type=doc_type,
                source_kind=source_kind,
                metadata={"sha256": hash_text(body)},
            ),
        )

    engram_root = repo_root / ".engram" / "rules"
    repo_rule = engram_root / "repo.md"
    if repo_rule.exists():
        body = _read_text(repo_rule)
        docs.append(
            DetectedDoc(
                path=repo_rule,
                title="Repo Rules",
                body=body,
                doc_type="rule",
                source_kind="repo_rule",
                metadata={"sha256": hash_text(body)},
            ),
        )

    agent_dir = engram_root / "agents"
    if agent_dir.exists():
        for path in sorted(agent_dir.glob("*.md")):
            body = _read_text(path)
            docs.append(
                DetectedDoc(
                    path=path,
                    title=path.stem,
                    body=body,
                    doc_type="rule",
                    source_kind="agent_rule",
                    scope_type="agent",
                    scope_key=path.stem,
                    metadata={"sha256": hash_text(body)},
                ),
            )

    paths_dir = engram_root / "paths"
    if paths_dir.exists():
        for path in sorted(paths_dir.glob("*.md")):
            body = _read_text(path)
            scope_key = path.stem.replace("__", "/")
            docs.append(
                DetectedDoc(
                    path=path,
                    title=scope_key,
                    body=body,
                    doc_type="rule",
                    source_kind="path_rule",
                    scope_type="path",
                    scope_key=scope_key,
                    metadata={"sha256": hash_text(body)},
                ),
            )

    branch_dir = engram_root / "branches"
    if branch_dir.exists():
        for path in sorted(branch_dir.glob("*.md")):
            body = _read_text(path)
            docs.append(
                DetectedDoc(
                    path=path,
                    title=path.stem,
                    body=body,
                    doc_type="rule",
                    source_kind="branch_rule",
                    scope_type="branch",
                    scope_key=path.stem,
                    metadata={"sha256": hash_text(body)},
                ),
            )

    return docs


def scan_global_rules() -> list[DetectedDoc]:
    docs: list[DetectedDoc] = []

    global_rule = config.global_rule_path()
    if global_rule.exists():
        body = _read_text(global_rule)
        docs.append(
            DetectedDoc(
                path=global_rule,
                title="Global Rules",
                body=body,
                doc_type="rule",
                source_kind="global_rule",
                scope_type="global",
                scope_key="global",
                metadata={"sha256": hash_text(body)},
            ),
        )

    global_agent_dir = config.global_rules_dir() / "agents"
    if global_agent_dir.exists():
        for path in sorted(global_agent_dir.glob("*.md")):
            body = _read_text(path)
            docs.append(
                DetectedDoc(
                    path=path,
                    title=path.stem,
                    body=body,
                    doc_type="rule",
                    source_kind="global_agent_rule",
                    scope_type="global_agent",
                    scope_key=path.stem,
                    metadata={"sha256": hash_text(body)},
                ),
            )

    return docs
