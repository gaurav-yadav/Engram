from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engram.mcp import TOOLS
from engram.project import initialize_project


class McpTests(unittest.TestCase):
    def test_project_show_returns_not_initialized_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"ENGRAM_HOME": str(home_root)}, clear=False):
                payload = TOOLS["project_show"][0]({"repo": str(repo_root)})

            self.assertEqual(payload["status"], "not_initialized")
            self.assertIn("engram auto-init", payload["message"])

    def test_document_search_tool_returns_indexed_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\nUseful ingestion guide.\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"ENGRAM_HOME": str(home_root)}, clear=False):
                initialize_project(
                    repo_root=repo_root,
                    seed_claude=False,
                    include_subagents=False,
                    since_days=None,
                )
                payload = TOOLS["document_search"][0](
                    {"repo": str(repo_root), "query": "ingestion", "limit": 5},
                )

            self.assertEqual(Path(payload["results"][0]["path"]).resolve(), (repo_root / "README.md").resolve())


if __name__ == "__main__":
    unittest.main()
