from __future__ import annotations

import json
import sqlite3
from pathlib import Path


COMMON_MIGRATIONS: list[tuple[str, str]] = [
    (
        "0001_base",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_path TEXT NOT NULL UNIQUE,
            repo_name TEXT NOT NULL,
            default_branch TEXT,
            language_summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scopes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            scope_type TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            parent_scope_id INTEGER,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, scope_type, scope_key),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(parent_scope_id) REFERENCES scopes(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id INTEGER NOT NULL,
            source_path TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            normalized_body TEXT NOT NULL,
            hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scope_id, source_path),
            FOREIGN KEY(scope_id) REFERENCES scopes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            scope_id INTEGER NOT NULL,
            doc_type TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, path),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(scope_id) REFERENCES scopes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS archive_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            agent_id TEXT,
            cwd TEXT,
            git_branch TEXT,
            is_sidechain INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            ended_at TEXT,
            source_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_path, file_hash),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS archive_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_session_id INTEGER NOT NULL,
            event_index INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            role TEXT,
            tool_name TEXT,
            content_text TEXT NOT NULL,
            content_json TEXT,
            timestamp TEXT,
            parent_uuid TEXT,
            uuid TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(archive_session_id) REFERENCES archive_sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            scope_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            source_key TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5,
            importance REAL NOT NULL DEFAULT 0.5,
            stability REAL NOT NULL DEFAULT 0.5,
            status TEXT NOT NULL DEFAULT 'active',
            valid_from TEXT,
            valid_to TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, scope_id, kind, source_key),
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(scope_id) REFERENCES scopes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memory_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            archive_session_id INTEGER,
            archive_event_id INTEGER,
            document_id INTEGER,
            source_excerpt TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(memory_id) REFERENCES memory_items(id) ON DELETE CASCADE,
            FOREIGN KEY(archive_session_id) REFERENCES archive_sessions(id) ON DELETE SET NULL,
            FOREIGN KEY(archive_event_id) REFERENCES archive_events(id) ON DELETE SET NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_scopes_project_type ON scopes(project_id, scope_type, priority);
        CREATE INDEX IF NOT EXISTS idx_rules_scope_active ON rules(scope_id, active);
        CREATE INDEX IF NOT EXISTS idx_documents_project_type ON documents(project_id, doc_type);
        CREATE INDEX IF NOT EXISTS idx_archive_sessions_project ON archive_sessions(project_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_archive_events_session ON archive_events(archive_session_id, event_index);
        CREATE INDEX IF NOT EXISTS idx_memory_items_project_kind ON memory_items(project_id, kind, status);
        """,
    ),
]

SQLITE_MIGRATIONS: list[tuple[str, str]] = [
    (
        "1001_fts",
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            doc_id UNINDEXED,
            title,
            body,
            path
        );
        """,
    ),
    (
        "1002_memory_fts",
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
            memory_id UNINDEXED,
            title,
            body,
            kind,
            source_key
        );

        CREATE INDEX IF NOT EXISTS idx_memory_provenance_memory ON memory_provenance(memory_id);
        """,
    ),
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        applied = {
            row["name"]
            for row in self.conn.execute("SELECT name FROM schema_migrations").fetchall()
        }
        for name, sql in [*COMMON_MIGRATIONS, *SQLITE_MIGRATIONS]:
            if name in applied:
                continue
            with self.conn:
                self.conn.executescript(sql)
                self.conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(name) VALUES (?)",
                    (name,),
                )

    def get_or_create_project(self, repo_path: Path) -> int:
        row = self.conn.execute(
            "SELECT id FROM projects WHERE repo_path = ?",
            (str(repo_path),),
        ).fetchone()
        if row:
            return int(row["id"])
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO projects(repo_path, repo_name)
                VALUES (?, ?)
                """,
                (str(repo_path), repo_path.name),
            )
        return int(cur.lastrowid)

    def get_project(self, repo_path: Path) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM projects
            WHERE repo_path = ?
            """,
            (str(repo_path.resolve()),),
        ).fetchone()

    def touch_project(self, project_id: int) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE projects
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (project_id,),
            )

    def ensure_scope(
        self,
        project_id: int,
        scope_type: str,
        scope_key: str,
        parent_scope_id: int | None = None,
        priority: int = 0,
    ) -> int:
        row = self.conn.execute(
            """
            SELECT id FROM scopes
            WHERE project_id = ? AND scope_type = ? AND scope_key = ?
            """,
            (project_id, scope_type, scope_key),
        ).fetchone()
        if row:
            return int(row["id"])
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO scopes(project_id, scope_type, scope_key, parent_scope_id, priority)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, scope_type, scope_key, parent_scope_id, priority),
            )
        return int(cur.lastrowid)

    def upsert_rule(
        self,
        scope_id: int,
        source_path: str,
        source_kind: str,
        title: str,
        body: str,
        body_hash: str,
    ) -> None:
        normalized_body = " ".join(body.split())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO rules(scope_id, source_path, source_kind, title, body, normalized_body, hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id, source_path)
                DO UPDATE SET
                    source_kind = excluded.source_kind,
                    title = excluded.title,
                    body = excluded.body,
                    normalized_body = excluded.normalized_body,
                    hash = excluded.hash,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (scope_id, source_path, source_kind, title, body, normalized_body, body_hash),
            )

    def upsert_document(
        self,
        project_id: int,
        scope_id: int,
        doc_type: str,
        path: str,
        title: str,
        body: str,
        metadata: dict[str, object] | None = None,
    ) -> int:
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO documents(project_id, scope_id, doc_type, path, title, body, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, path)
                DO UPDATE SET
                    scope_id = excluded.scope_id,
                    doc_type = excluded.doc_type,
                    title = excluded.title,
                    body = excluded.body,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (project_id, scope_id, doc_type, path, title, body, metadata_json),
            )
            row = self.conn.execute(
                "SELECT id FROM documents WHERE project_id = ? AND path = ?",
                (project_id, path),
            ).fetchone()
            document_id = int(row["id"])
            self.conn.execute(
                "DELETE FROM documents_fts WHERE doc_id = ?",
                (document_id,),
            )
            self.conn.execute(
                """
                INSERT INTO documents_fts(doc_id, title, body, path)
                VALUES (?, ?, ?, ?)
                """,
                (document_id, title, body, path),
            )
        return document_id

    def archive_session_exists(self, source_path: str, file_hash: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM archive_sessions
            WHERE source_path = ? AND file_hash = ?
            """,
            (source_path, file_hash),
        ).fetchone()
        return row is not None

    def insert_archive_session(
        self,
        project_id: int,
        session_id: str,
        agent_id: str | None,
        cwd: str | None,
        git_branch: str | None,
        is_sidechain: bool,
        started_at: str | None,
        ended_at: str | None,
        source_path: str,
        file_hash: str,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO archive_sessions(
                    project_id, source, session_id, agent_id, cwd, git_branch,
                    is_sidechain, started_at, ended_at, source_path, file_hash
                )
                VALUES (?, 'claude', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    session_id,
                    agent_id,
                    cwd,
                    git_branch,
                    int(is_sidechain),
                    started_at,
                    ended_at,
                    source_path,
                    file_hash,
                ),
            )
        return int(cur.lastrowid)

    def insert_archive_events(
        self,
        archive_session_id: int,
        events: list[tuple[int, str, str | None, str | None, str, str | None, str | None, str | None, str | None]],
    ) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO archive_events(
                    archive_session_id, event_index, event_type, role, tool_name,
                    content_text, content_json, timestamp, parent_uuid, uuid
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        archive_session_id,
                        event_index,
                        event_type,
                        role,
                        tool_name,
                        content_text,
                        content_json,
                        timestamp,
                        parent_uuid,
                        uuid,
                    )
                    for (
                        event_index,
                        event_type,
                        role,
                        tool_name,
                        content_text,
                        content_json,
                        timestamp,
                        parent_uuid,
                        uuid,
                    ) in events
                ],
            )

    def upsert_memory_item(
        self,
        project_id: int,
        scope_id: int,
        kind: str,
        title: str,
        body: str,
        source_key: str,
        confidence: float = 0.5,
        importance: float = 0.5,
        stability: float = 0.5,
    ) -> int:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO memory_items(
                    project_id, scope_id, kind, title, body, source_key,
                    confidence, importance, stability
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, scope_id, kind, source_key)
                DO UPDATE SET
                    title = excluded.title,
                    body = excluded.body,
                    confidence = excluded.confidence,
                    importance = excluded.importance,
                    stability = excluded.stability,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_id,
                    scope_id,
                    kind,
                    title,
                    body,
                    source_key,
                    confidence,
                    importance,
                    stability,
                ),
            )
            row = self.conn.execute(
                """
                SELECT id FROM memory_items
                WHERE project_id = ? AND scope_id = ? AND kind = ? AND source_key = ?
                """,
                (project_id, scope_id, kind, source_key),
            ).fetchone()
            memory_id = int(row["id"])
            self.conn.execute(
                "DELETE FROM memory_items_fts WHERE memory_id = ?",
                (memory_id,),
            )
            self.conn.execute(
                """
                INSERT INTO memory_items_fts(memory_id, title, body, kind, source_key)
                VALUES (?, ?, ?, ?, ?)
                """,
                (memory_id, title, body, kind, source_key),
            )
        return memory_id

    def replace_memory_provenance(
        self,
        memory_id: int,
        provenance_rows: list[tuple[int | None, int | None, int | None, str | None]],
    ) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM memory_provenance WHERE memory_id = ?",
                (memory_id,),
            )
            self.conn.executemany(
                """
                INSERT INTO memory_provenance(
                    memory_id, archive_session_id, archive_event_id, document_id, source_excerpt
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (memory_id, archive_session_id, archive_event_id, document_id, source_excerpt)
                    for archive_session_id, archive_event_id, document_id, source_excerpt in provenance_rows
                ],
            )

    def delete_memory_items(self, project_id: int, scope_id: int, kind: str) -> None:
        rows = self.conn.execute(
            """
            SELECT id
            FROM memory_items
            WHERE project_id = ? AND scope_id = ? AND kind = ?
            """,
            (project_id, scope_id, kind),
        ).fetchall()
        with self.conn:
            for row in rows:
                self.conn.execute(
                    "DELETE FROM memory_items_fts WHERE memory_id = ?",
                    (int(row["id"]),),
                )
            self.conn.execute(
                """
                DELETE FROM memory_items
                WHERE project_id = ? AND scope_id = ? AND kind = ?
                """,
                (project_id, scope_id, kind),
            )

    def list_memory_items(self, project_id: int, kind: str | None = None) -> list[sqlite3.Row]:
        if kind:
            rows = self.conn.execute(
                """
                SELECT m.*, s.scope_type, s.scope_key
                FROM memory_items m
                JOIN scopes s ON s.id = m.scope_id
                WHERE project_id = ? AND kind = ?
                ORDER BY importance DESC, title ASC
                """,
                (project_id, kind),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT m.*, s.scope_type, s.scope_key
                FROM memory_items m
                JOIN scopes s ON s.id = m.scope_id
                WHERE m.project_id = ?
                ORDER BY kind ASC, importance DESC, title ASC
                """,
                (project_id,),
            ).fetchall()
        return list(rows)

    def get_memory_item(self, project_id: int, memory_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT m.*, s.scope_type, s.scope_key
            FROM memory_items m
            JOIN scopes s ON s.id = m.scope_id
            WHERE m.project_id = ? AND m.id = ?
            """,
            (project_id, memory_id),
        ).fetchone()

    def delete_memory_item(self, project_id: int, memory_id: int) -> bool:
        row = self.conn.execute(
            """
            SELECT id
            FROM memory_items
            WHERE project_id = ? AND id = ?
            """,
            (project_id, memory_id),
        ).fetchone()
        if row is None:
            return False
        with self.conn:
            self.conn.execute(
                "DELETE FROM memory_items_fts WHERE memory_id = ?",
                (memory_id,),
            )
            self.conn.execute(
                """
                DELETE FROM memory_items
                WHERE project_id = ? AND id = ?
                """,
                (project_id, memory_id),
            )
        return True

    def search_memory(
        self,
        project_id: int,
        query: str,
        kind: str | None = None,
        limit: int = 10,
    ) -> list[sqlite3.Row]:
        params: list[object] = [project_id, query]
        kind_filter = ""
        if kind:
            kind_filter = " AND m.kind = ?"
            params.append(kind)
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT
                m.*,
                s.scope_type,
                s.scope_key,
                bm25(memory_items_fts) AS rank
            FROM memory_items_fts
            JOIN memory_items m ON m.id = memory_items_fts.memory_id
            JOIN scopes s ON s.id = m.scope_id
            WHERE m.project_id = ?
              AND memory_items_fts MATCH ?
              {kind_filter}
            ORDER BY rank ASC, m.importance DESC, m.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return list(rows)

    def search_documents(
        self,
        project_id: int,
        query: str,
        doc_type: str | None = None,
        limit: int = 10,
    ) -> list[sqlite3.Row]:
        params: list[object] = [project_id, query]
        type_filter = ""
        if doc_type:
            type_filter = " AND d.doc_type = ?"
            params.append(doc_type)
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT
                d.*,
                s.scope_type,
                s.scope_key,
                bm25(documents_fts) AS rank
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.doc_id
            JOIN scopes s ON s.id = d.scope_id
            WHERE d.project_id = ?
              AND documents_fts MATCH ?
              {type_filter}
            ORDER BY rank ASC, d.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return list(rows)

    def project_stats(self, project_id: int) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM documents WHERE project_id = ?) AS documents_count,
                (SELECT COUNT(*) FROM rules r JOIN scopes s ON s.id = r.scope_id WHERE s.project_id = ?) AS rules_count,
                (SELECT COUNT(*) FROM memory_items WHERE project_id = ?) AS memory_count,
                (SELECT COUNT(*) FROM archive_sessions WHERE project_id = ?) AS archive_sessions_count,
                (SELECT COUNT(*) FROM archive_events WHERE archive_session_id IN (
                    SELECT id FROM archive_sessions WHERE project_id = ?
                )) AS archive_events_count
            """,
            (project_id, project_id, project_id, project_id, project_id),
        ).fetchone()
        return {
            "documents_count": int(row["documents_count"]),
            "rules_count": int(row["rules_count"]),
            "memory_count": int(row["memory_count"]),
            "archive_sessions_count": int(row["archive_sessions_count"]),
            "archive_events_count": int(row["archive_events_count"]),
        }

    def get_memory_provenance(self, memory_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT *
                FROM memory_provenance
                WHERE memory_id = ?
                ORDER BY id ASC
                """,
                (memory_id,),
            ).fetchall(),
        )

    def list_archive_sessions(self, project_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM archive_sessions
                WHERE project_id = ?
                ORDER BY started_at ASC, id ASC
                """,
                (project_id,),
            ).fetchall(),
        )
