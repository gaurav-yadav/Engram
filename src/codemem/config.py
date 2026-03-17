from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = ".codemem"


def home_dir() -> Path:
    return Path.home()


def global_state_dir() -> Path:
    override = os.environ.get("CODEMEM_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return home_dir() / APP_DIR_NAME


def global_config_path() -> Path:
    return global_state_dir() / "config.yaml"


def db_path() -> Path:
    return global_state_dir() / "codemem.db"


def global_rules_dir() -> Path:
    return global_state_dir() / "rules"


def global_rule_path() -> Path:
    return global_rules_dir() / "global.md"


def claude_projects_dir() -> Path:
    return home_dir() / ".claude" / "projects"


def repo_state_dir(repo_root: Path) -> Path:
    return repo_root / APP_DIR_NAME


def ensure_global_layout() -> None:
    base = global_state_dir()
    for child in ("logs", "cache", "projects", "locks", "backups"):
        (base / child).mkdir(parents=True, exist_ok=True)
    (base / "rules").mkdir(parents=True, exist_ok=True)
    (base / "rules" / "agents").mkdir(parents=True, exist_ok=True)


def ensure_default_global_config() -> Path:
    ensure_global_layout()
    path = global_config_path()
    if not path.exists():
        path.write_text(
            "\n".join(
                [
                    "database:",
                    f"  backend: sqlite",
                    f"  path: {db_path()}",
                    "tools:",
                    "  git_path: git",
                    "  rg_path: rg",
                    "models:",
                    "  provider: null",
                    "  base_url: null",
                    "  extraction_model: null",
                    "  rerank_model: null",
                    "defaults:",
                    "  enable_archive_import: true",
                    "  include_subagents: true",
                    "  index_on_init: true",
                    "",
                ],
            ),
            encoding="utf-8",
        )
    global_rule = global_rule_path()
    if not global_rule.exists():
        global_rule.write_text(
            "\n".join(
                [
                    "# Global Rules",
                    "",
                    "<!-- Add persistent cross-repo coding rules here. -->",
                    "",
                ],
            ),
            encoding="utf-8",
        )
    return path
