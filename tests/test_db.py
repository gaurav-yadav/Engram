from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codemem.db import Database


class DatabaseTests(unittest.TestCase):
    def test_migrations_and_project_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "codemem.db")
            db.migrate()
            project_id = db.get_or_create_project(Path(tmp) / "repo")
            self.assertGreater(project_id, 0)
            scope_id = db.ensure_scope(project_id, "repo", str(Path(tmp) / "repo"))
            self.assertGreater(scope_id, 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
