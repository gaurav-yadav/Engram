from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engram.doctor import run


class DoctorTests(unittest.TestCase):
    def test_repo_checks_are_included(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checks = run(Path(tmp))
            names = {check.name for check in checks}
            self.assertIn("git", names)
            self.assertIn("rg", names)
            self.assertIn("repo_exists", names)
            self.assertIn("repo_access", names)
            self.assertIn("project_initialized", names)


if __name__ == "__main__":
    unittest.main()
