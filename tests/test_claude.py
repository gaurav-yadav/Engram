from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engram.claude import parse_session_file


class ClaudeImportTests(unittest.TestCase):
    def test_parse_session_file_extracts_bash_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "user",
                                "message": {"role": "user", "content": "Fix the failing tests"},
                                "cwd": "/tmp/repo",
                                "sessionId": "abc123",
                                "timestamp": "2026-03-17T10:00:00Z",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "tool_use",
                                            "name": "Bash",
                                            "input": {"command": "pytest -q"},
                                        }
                                    ],
                                },
                                "cwd": "/tmp/repo",
                                "sessionId": "abc123",
                                "timestamp": "2026-03-17T10:01:00Z",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            session = parse_session_file(path)
            self.assertEqual(session.session_id, "abc123")
            self.assertEqual(session.cwd, "/tmp/repo")
            self.assertEqual(len(session.events), 2)
            self.assertEqual(session.events[1].tool_name, "Bash")


if __name__ == "__main__":
    unittest.main()
