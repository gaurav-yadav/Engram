from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from engram import config
from engram.cli import main
from engram.db import Database
from engram.project import initialize_project


class CliTests(unittest.TestCase):
    def _run_main(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_project_show_infers_repo_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"ENGRAM_HOME": str(home_root)}, clear=False):
                initialize_project(
                    repo_root=repo_root,
                    seed_claude=False,
                    include_subagents=False,
                    since_days=None,
                )
                previous = Path.cwd()
                os.chdir(repo_root)
                try:
                    code, stdout, stderr = self._run_main(["project", "show"])
                finally:
                    os.chdir(previous)

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertIn("Project: repo", stdout)

    def test_auto_init_initializes_repo_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"ENGRAM_HOME": str(home_root)}, clear=False):
                previous = Path.cwd()
                os.chdir(repo_root)
                try:
                    code, _, stderr = self._run_main(["auto-init"])
                finally:
                    os.chdir(previous)

                db = Database(config.db_path())
                db.migrate()
                try:
                    project = db.get_project(repo_root)
                finally:
                    db.close()

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertTrue((repo_root / ".engram" / "project.yaml").exists())
            self.assertIsNotNone(project)

    def test_sync_initializes_repo_from_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"ENGRAM_HOME": str(home_root)}, clear=False):
                previous = Path.cwd()
                os.chdir(repo_root)
                try:
                    code, stdout, stderr = self._run_main(["sync"])
                finally:
                    os.chdir(previous)

                db = Database(config.db_path())
                db.migrate()
                try:
                    project = db.get_project(repo_root)
                finally:
                    db.close()

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertIn("Synchronized project memory for", stdout)
            self.assertIsNotNone(project)

    def test_setup_hooks_writes_script_and_settings_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home_root = Path(tmp) / "home"
            engram_home = Path(tmp) / "engram-home"
            home_root.mkdir(parents=True)

            with mock.patch.dict(
                os.environ,
                {"HOME": str(home_root), "ENGRAM_HOME": str(engram_home)},
                clear=False,
            ):
                code1, stdout1, stderr1 = self._run_main(["setup-hooks"])
                code2, stdout2, stderr2 = self._run_main(["setup-hooks"])

            script = (engram_home / "bin" / "engram-auto-init.sh").resolve()
            settings_path = home_root / ".claude" / "settings.json"
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            hooks = settings["hooks"]["SessionStart"]
            command = f"bash {script}"

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(stderr1, "")
            self.assertEqual(stderr2, "")
            self.assertIn("Wrote hook script", stdout1)
            self.assertIn("Updated settings", stdout2)
            self.assertTrue(script.exists())
            self.assertEqual(sum(1 for hook in hooks if hook["command"] == command), 1)

    def test_docs_search_supports_inferred_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\nUseful onboarding details.\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"ENGRAM_HOME": str(home_root)}, clear=False):
                initialize_project(
                    repo_root=repo_root,
                    seed_claude=False,
                    include_subagents=False,
                    since_days=None,
                )
                previous = Path.cwd()
                os.chdir(repo_root)
                try:
                    code, stdout, stderr = self._run_main(["docs", "search", "onboarding"])
                finally:
                    os.chdir(previous)

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertIn("README.md", stdout)

    def test_memory_cli_store_list_delete_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            home_root = Path(tmp) / "home"
            repo_root.mkdir(parents=True)
            (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"ENGRAM_HOME": str(home_root)}, clear=False):
                initialize_project(
                    repo_root=repo_root,
                    seed_claude=False,
                    include_subagents=False,
                    since_days=None,
                )
                store_code, store_stdout, store_stderr = self._run_main(
                    [
                        "memory",
                        "store",
                        "note",
                        "Sync Workflow",
                        "Run engram sync before coding sessions.",
                        "--repo",
                        str(repo_root),
                        "--json",
                    ]
                )
                store_payload = json.loads(store_stdout)
                memory_id = int(store_payload["memory"]["id"])

                list_code, list_stdout, list_stderr = self._run_main(
                    ["memory", "list", "--repo", str(repo_root)]
                )
                delete_code, delete_stdout, delete_stderr = self._run_main(
                    ["memory", "delete", str(memory_id), "--repo", str(repo_root)]
                )

            self.assertEqual(store_code, 0)
            self.assertEqual(store_stderr, "")
            self.assertEqual(list_code, 0)
            self.assertEqual(list_stderr, "")
            self.assertIn("Sync Workflow", list_stdout)
            self.assertEqual(delete_code, 0)
            self.assertEqual(delete_stderr, "")
            self.assertTrue(re.search(rf"Deleted memory #{memory_id}\b", delete_stdout))


if __name__ == "__main__":
    unittest.main()
