from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codemem import config
from codemem.db import Database
from codemem.project import initialize_project
from codemem.query import get_applicable_rules, search_memory
from codemem.repoindex import ensure_repo_layout


class QueryTests(unittest.TestCase):
    def test_rules_resolve_global_repo_path_and_agent_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")
            ensure_repo_layout(repo_root)

            with mock.patch.dict(os.environ, {"CODEMEM_HOME": str(home_root)}, clear=False):
                config.ensure_default_global_config()
                config.global_rule_path().write_text("# Global\nUse local-first defaults.\n", encoding="utf-8")
                (repo_root / ".codemem" / "rules" / "repo.md").write_text(
                    "# Repo\nPrefer SQLite for local mode.\n",
                    encoding="utf-8",
                )
                (repo_root / ".codemem" / "rules" / "paths" / "src__codemem.md").write_text(
                    "# Path\nKeep CLI output concise.\n",
                    encoding="utf-8",
                )
                (repo_root / ".codemem" / "rules" / "agents" / "reviewer.md").write_text(
                    "# Reviewer\nLead with findings.\n",
                    encoding="utf-8",
                )

                initialize_project(
                    repo_root=repo_root,
                    seed_claude=False,
                    include_subagents=False,
                    since_days=None,
                )

                db = Database(config.db_path())
                db.migrate()
                try:
                    payload = get_applicable_rules(
                        db=db,
                        repo_root=repo_root,
                        target_path="src/codemem/cli.py",
                        agent="reviewer",
                    )
                finally:
                    db.close()

            scope_types = [rule["scope_type"] for rule in payload["rules"]]
            self.assertEqual(scope_types, ["global", "repo", "path", "agent"])

    def test_memory_search_returns_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"CODEMEM_HOME": str(home_root)}, clear=False):
                initialize_project(
                    repo_root=repo_root,
                    seed_claude=False,
                    include_subagents=False,
                    since_days=None,
                )
                db = Database(config.db_path())
                db.migrate()
                try:
                    project = db.get_project(repo_root)
                    self.assertIsNotNone(project)
                    project_id = int(project["id"])
                    repo_scope = db.conn.execute(
                        """
                        SELECT id
                        FROM scopes
                        WHERE project_id = ? AND scope_type = 'repo' AND scope_key = ?
                        """,
                        (project_id, str(repo_root.resolve())),
                    ).fetchone()
                    self.assertIsNotNone(repo_scope)
                    memory_id = db.upsert_memory_item(
                        project_id=project_id,
                        scope_id=int(repo_scope["id"]),
                        kind="preference",
                        title="Prefer local-only defaults",
                        body="Prefer local-only defaults for coding memory.",
                        source_key="pref:test",
                    )
                    db.replace_memory_provenance(
                        memory_id,
                        [(None, None, None, "Prefer local-only defaults for coding memory.")],
                    )
                    payload = search_memory(
                        db=db,
                        repo_root=repo_root,
                        query="local defaults",
                        kind="preference",
                        limit=5,
                    )
                finally:
                    db.close()

            self.assertEqual(len(payload["results"]), 1)
            self.assertEqual(payload["results"][0]["kind"], "preference")
            self.assertEqual(payload["results"][0]["provenance"][0]["source_excerpt"], "Prefer local-only defaults for coding memory.")


if __name__ == "__main__":
    unittest.main()
