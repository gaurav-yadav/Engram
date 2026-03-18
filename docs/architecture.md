# Engram System Architecture

Version: 0.1.0

---

## 1. Problem Statement

AI coding assistants -- Claude, Codex, Copilot, and their descendants -- operate in
a fundamentally stateless mode. Each session starts from scratch. The assistant has no
memory of what it learned in the last session: which test commands work, what the user
prefers, which architectural decisions were made, or what rules govern the codebase.

This creates three concrete problems:

1. **Lost context across sessions.** An assistant that debugged a test suite yesterday
   does not remember the fix today. A user who explained "always use `uv run pytest`"
   must re-explain it every time.

2. **No persistent rules.** Codebases have conventions -- formatting rules, naming
   patterns, forbidden patterns, branch-specific workflows. These live in human heads
   or scattered markdown files. Assistants cannot deterministically load and apply them.

3. **No provenance for learned knowledge.** Even when an assistant does remember
   something (via prompt injection or system prompts), there is no audit trail linking
   that knowledge back to the session, command, or conversation where it originated.

Engram solves these problems by providing a local, deterministic memory layer that
ingests existing assistant history, indexes project rules at multiple scopes, distills
recurring patterns into searchable memory items with provenance, and exposes all of this
through both a CLI and a Model Context Protocol (MCP) server that any compatible
assistant can query.

---

## 2. Design Principles

**Local-first.** All data stays on disk. The database is a single SQLite file.
There is no cloud service, no remote API, no telemetry. The `ENGRAM_HOME` environment
variable controls where global state lives; it defaults to `~/.engram`.

**Stdlib-only.** The Python package declares zero dependencies (`dependencies = []`
in `pyproject.toml`). Every module uses only the Python standard library. This keeps
the tool installable as a single `.pyz` zipapp on any machine with Python 3.9+.

**SQLite with WAL and FTS5.** The database uses Write-Ahead Logging for concurrent
read safety and FTS5 virtual tables for full-text search over documents and memory
items. Migrations are applied on every connection via a versioned migration table.

**No cloud dependency.** The tool works fully offline. Claude chat history is imported
from local `~/.claude/projects` JSONL files, not from any API.

**Deterministic rules.** Rule resolution is not probabilistic or model-driven. Given
a project, target path, agent, branch, and session key, the exact set of applicable
rules is determined by a fixed scope priority order and SQL lookups. The same inputs
always produce the same rules.

**Provenance tracking.** Every distilled memory item (promoted command, promoted
preference) links back to the specific archive session and event that generated it
via the `memory_provenance` table. This makes it possible to trace any piece of
remembered knowledge to its origin.

---

## 3. System Architecture

### High-Level Component Diagram

```
+------------------------------------------------------------------+
|                         User / AI Client                         |
+------------------------------------------------------------------+
        |                        |                        |
        | CLI (argparse)         | MCP (stdio JSON-RPC)   | HTTP
        v                        v                        v
+----------------+    +-------------------+    +------------------+
|    cli.py      |    |     mcp.py        |    |   cli.py serve   |
|  (subcommands) |    | (stdio server)    |    |  (health only)   |
+----------------+    +-------------------+    +------------------+
        |                        |
        +----------+-------------+
                   |
                   v
        +--------------------+
        |     query.py       |
        | (context assembly, |
        |  rule resolution,  |
        |  memory search)    |
        +--------------------+
           |            |
           v            v
   +-----------+  +------------+
   | rules.py  |  | config.py  |
   | (scoped   |  | (paths,    |
   |  rule     |  |  env vars) |
   |  loading) |  +------------+
   +-----------+
           |
           v
   +------------------+
   |      db.py       |
   | (SQLite, WAL,    |
   |  FTS5, migrate)  |
   +------------------+
           |
           v
   +------------------+
   | ~/.engram/       |
   |   engram.db      |
   +------------------+
```

### Initialization Data Flow

```
engram init /path/to/repo --seed-claude
        |
        v
+------------------+
|   project.py     |
| initialize_project()
+------------------+
        |
        +---> config.ensure_default_global_config()
        |         Creates ~/.engram/ layout, default config.yaml, global.md
        |
        +---> repoindex.ensure_repo_layout(repo_root)
        |         Creates .engram/ tree: rules/, summaries/, imports/, cache/
        |
        +---> db.Database(config.db_path())
        |     db.migrate()
        |         Applies COMMON_MIGRATIONS + SQLITE_MIGRATIONS
        |
        +---> db.get_or_create_project(repo_root)
        |         Ensures project row in projects table
        |
        +---> repoindex.scan_global_rules()
        |     repoindex.scan_repo(repo_root)
        |         Discovers: README, AGENTS.md, CLAUDE.md, pyproject.toml,
        |         package.json, Cargo.toml, Makefile, .engram/rules/**/*.md
        |         Each becomes a DetectedDoc with scope_type and doc_type
        |
        +---> For each DetectedDoc:
        |         db.ensure_scope()       -- create scope row
        |         db.upsert_document()    -- insert doc + FTS index
        |         db.upsert_rule()        -- if doc_type == "rule"
        |
        +---> claude.import_claude_history()  (if --seed-claude)
        |         Scans ~/.claude/projects/**/*.jsonl
        |         Filters by repo CWD match, recency, dedup by file_hash
        |         Inserts archive_sessions + archive_events
        |         Promotes commands and preferences into memory_items
        |
        +---> summary.write_summaries()
                  Writes project-summary.md, commands.md, preferences.md,
                  claude-seed-manifest.json into .engram/summaries/ and
                  .engram/imports/
```

### Query / Retrieval Data Flow

```
engram context /path/to/repo "failing tests" --path src/api.py --agent reviewer
        |
        v
+------------------+
|    query.py      |
| build_context()  |
+------------------+
        |
        +---> get_applicable_rules()
        |         rules.load_applicable_rules(conn, project_id, ...)
        |         Resolves scopes: global -> repo -> path -> global_agent
        |                          -> agent -> branch -> session
        |         Returns all active rules matching requested scopes
        |
        +---> db.search_memory(project_id, fts_query, limit)
        |         FTS5 MATCH on memory_items_fts
        |         Ranked by bm25() + importance + recency
        |
        +---> db.search_documents(project_id, fts_query, limit)
        |         FTS5 MATCH on documents_fts
        |         Ranked by bm25() + recency
        |
        +---> _read_summary(repo_root, "project-summary.md")
        |
        +---> Returns assembled context bundle:
                  { summary, rules[], memory[], documents[] }
```

---

## 4. Core Components

### 4.1 CLI Layer (`src/engram/cli.py`)

The CLI is built on `argparse` with subcommands. The entry point is `engram.cli:main`,
registered as a console script in `pyproject.toml`.

| Subcommand       | Handler              | Description                                       |
|------------------|----------------------|---------------------------------------------------|
| `doctor`         | `_cmd_doctor`        | Validates git, rg, writable state dirs, Claude history |
| `init`           | `_cmd_init`          | Bootstraps repo: index docs, rules, import Claude  |
| `sync`           | `_cmd_sync`          | Refreshes indexed docs, rules, summaries, and optional Claude imports |
| `project show`   | `_cmd_project_show`  | Displays project stats and summaries               |
| `rules show`     | `_cmd_rules_show`    | Resolves and displays applicable rules             |
| `memory search`  | `_cmd_memory_search` | FTS search over distilled memory items             |
| `memory list`    | `_cmd_memory_list`   | Lists stored project memory                        |
| `memory store`   | `_cmd_memory_store`  | Stores a manual project memory item                |
| `memory delete`  | `_cmd_memory_delete` | Deletes a stored project memory item by ID         |
| `docs search`    | `_cmd_docs_search`   | Searches indexed project documents                 |
| `context`        | `_cmd_context`       | Assembles full context bundle for a query          |
| `mcp`            | `_cmd_mcp`           | Launches the MCP stdio server                      |
| `serve`          | `_cmd_serve`         | Runs a minimal HTTP health-check server            |

All retrieval commands accept `--json` for machine-readable output. Human-readable
formatters (`_format_rules`, `_format_memory`, `_format_context`, `_format_project`)
handle the default text output.

### 4.2 MCP Server (`src/engram/mcp.py`)

The MCP server implements the Model Context Protocol over stdio using newline-delimited
JSON-RPC 2.0. It reads one message per line from stdin (with fallback support for
Content-Length framed messages) and writes one JSON object per line to stdout.

**Transport details:**

- Input: reads lines from `sys.stdin.buffer`. If a line starts with `{`, it is parsed
  as bare JSON. Otherwise, the server falls back to Content-Length header framing
  (reads headers until blank line, then reads the body by length).
- Output: each response is a single `json.dumps(payload)` followed by `\n`, written
  to `sys.stdout.buffer` and flushed immediately.

**Protocol messages handled:**

| Method                    | Behavior                                              |
|---------------------------|-------------------------------------------------------|
| `initialize`              | Returns server info, capabilities, protocol version   |
| `notifications/initialized` | Acknowledged silently (no response)                |
| `ping`                    | Returns empty success                                 |
| `tools/list`              | Returns the tool catalog with schemas                 |
| `tools/call`              | Dispatches to the named tool handler                  |
| `shutdown`                | Returns success and exits the server loop             |

**Exposed tools:** `doctor`, `project_show`, `project_sync`, `rules_show`,
`memory_search`, `memory_list`, `memory_store`, `memory_delete`,
`document_search`, and `context_build`. Each tool handler validates its
arguments, opens a database connection via `_with_db` where needed, delegates
to `query.py` or `project.py`, and returns the result as a JSON text content
block.

### 4.3 Database Layer (`src/engram/db.py`)

The `Database` class wraps a single `sqlite3.Connection` and provides typed methods
for all data operations.

**Connection configuration:**

```python
self.conn.execute("PRAGMA journal_mode=WAL")
self.conn.execute("PRAGMA foreign_keys=ON")
```

WAL mode enables concurrent readers without blocking writes. Foreign keys are
enforced for referential integrity.

**Migration system:**

Migrations are stored as `(name, sql)` tuples in two lists:

- `COMMON_MIGRATIONS` -- core schema (tables, indexes)
- `SQLITE_MIGRATIONS` -- SQLite-specific features (FTS5 virtual tables)

A `schema_migrations` table tracks which migrations have been applied. On each
`db.migrate()` call, unapplied migrations run in order. This is called on every
database open.

**FTS5 virtual tables:**

- `documents_fts` -- indexes `title`, `body`, `path` for document search
- `memory_items_fts` -- indexes `title`, `body`, `kind`, `source_key` for memory search

FTS entries are maintained manually: on every upsert, the old FTS row is deleted
and a new one inserted. Search uses `bm25()` ranking.

**Key operations:**

- `get_or_create_project` -- idempotent project registration
- `ensure_scope` -- idempotent scope creation with parent linkage
- `upsert_rule` -- insert-or-update with conflict on `(scope_id, source_path)`
- `upsert_document` -- insert-or-update with FTS re-index
- `archive_session_exists` -- dedup check by `(source_path, file_hash)`
- `insert_archive_session` / `insert_archive_events` -- bulk archive import
- `upsert_memory_item` -- insert-or-update with FTS re-index
- `replace_memory_provenance` -- delete-and-reinsert provenance links
- `search_memory` / `search_documents` -- FTS5 MATCH queries with optional filters

### 4.4 Scoped Rules Engine (`src/engram/rules.py`, `src/engram/repoindex.py`)

Rules are deterministic, file-backed instructions loaded from markdown files at
multiple scope levels. The scope hierarchy, from broadest to narrowest:

| Priority | Scope Type     | Source Location                              | Scope Key          |
|----------|---------------|----------------------------------------------|--------------------|
| 10       | `global`       | `~/.engram/rules/global.md`                  | `"global"`         |
| 20       | `repo`         | `.engram/rules/repo.md`, `AGENTS.md`, `CLAUDE.md` | repo absolute path |
| 30       | `path`         | `.engram/rules/paths/<encoded>.md`           | relative file path |
| 35       | `global_agent` | `~/.engram/rules/agents/<name>.md`           | agent name         |
| 40       | `agent`        | `.engram/rules/agents/<name>.md`             | agent name         |
| 50       | `branch`       | `.engram/rules/branches/<name>.md`           | branch name        |
| 60       | `session`      | (not yet file-backed)                        | session key        |

**Rule resolution algorithm** (`rules.load_applicable_rules`):

1. Always include `global` and `repo` scopes.
2. If a `target_path` is provided, resolve it relative to the repo root and generate
   path scope candidates for every directory prefix (e.g., `src` then `src/engram`
   then `src/engram/cli.py`).
3. If an `agent` is provided, include both `global_agent` and `agent` scopes.
4. If a `branch` is provided, include the `branch` scope.
5. If a `session_key` is provided, include the `session` scope.
6. For each requested `(scope_type, scope_key)`, query all active rules ordered by
   scope priority then source path.

**Repo indexing** (`repoindex.scan_repo`):

Scans the repository root for well-known files:

- `README.md`, `README.rst`, `README.txt` -- indexed as `readme` documents
- `AGENTS.md`, `CLAUDE.md` -- indexed as `rule` documents at repo scope
- `pyproject.toml`, `package.json`, `Cargo.toml`, `Makefile` -- indexed as `manifest` documents
- `.engram/rules/repo.md` -- repo-level rule
- `.engram/rules/agents/*.md` -- per-agent rules
- `.engram/rules/paths/*.md` -- per-path rules (filename `__` encodes `/`)
- `.engram/rules/branches/*.md` -- per-branch rules

Global indexing (`repoindex.scan_global_rules`) scans `~/.engram/rules/global.md`
and `~/.engram/rules/agents/*.md`.

### 4.5 Claude Archive Import (`src/engram/claude.py`)

Imports session history from Claude Code's local JSONL archive at
`~/.claude/projects/`. Each `.jsonl` file represents one session.

**Session parsing** (`parse_session_file`):

- Reads line-by-line, computing a rolling SHA-256 hash of the raw bytes
- Extracts metadata: `sessionId`, `agentId`, `cwd`, `gitBranch`, `isSidechain`,
  timestamps, `parentUuid`, `uuid`
- Decomposes each record's `message.content` blocks into typed events:
  `user_text`, `assistant_text`, `assistant_thinking`, `assistant_tool_use`,
  `assistant_tool_result`, `queue_*`
- Returns a `ClaudeSession` dataclass with all extracted events

**Session filtering:**

- `_session_matches_repo` -- the session's most-common `cwd` must be inside the
  target repo root. Sidechain (sub-agent) sessions are excluded unless
  `--include-subagents` is set.
- `_session_is_recent_enough` -- the session's start timestamp must be within
  `--since` days (default 180).
- Deduplication by `(source_path, file_hash)` -- re-running init skips
  already-imported sessions.

**Memory promotion:**

After importing raw sessions, two promotion passes extract distilled knowledge:

1. **Command promotion** (`_promote_command_memories`): scans all `assistant_tool_use`
   events where `tool_name == "bash"`, extracts the `command` field, counts
   occurrences, filters out noisy commands (cat, grep, ls, etc.), keeps commands
   that either appear 2+ times or match useful tool patterns (pytest, mypy, cargo,
   docker, etc.), and stores up to 50 as `memory_items` with kind `"command"`.
   Confidence, importance, and stability scores scale with occurrence count.

2. **Preference promotion** (`_promote_preference_memories`): scans all `user_text`
   events for sentences matching preference hint patterns ("I prefer", "always",
   "never", "avoid", etc.), counts normalized occurrences, and stores up to 30
   as `memory_items` with kind `"preference"`.

Both promoters attach provenance records linking each memory item to up to 3
source archive events.

### 4.6 Query and Retrieval (`src/engram/query.py`)

Provides three retrieval APIs used by both CLI and MCP:

- **`get_applicable_rules(db, repo_root, target_path, agent, branch, session_key)`**
  Delegates to `rules.load_applicable_rules`. Returns the full list of active rules
  matching the requested scope parameters.

- **`search_memory(db, repo_root, query, kind, limit)`**
  Sanitizes the query into quoted FTS5 tokens, runs `db.search_memory`, and enriches
  each result with provenance records.

- **`build_context(db, repo_root, query, ...)`**
  The highest-level retrieval function. Assembles a complete context bundle containing:
  - The project summary (from `.engram/summaries/project-summary.md`)
  - All applicable rules for the given scope parameters
  - Top matching memory items via FTS
  - Top matching documents via FTS

  This is the primary interface for AI clients that need a single call to get
  everything relevant to a coding question.

FTS query sanitization (`_safe_fts_query`) extracts alphanumeric tokens and wraps
each in double quotes to prevent FTS5 syntax errors from user input.

### 4.7 Summary Generation (`src/engram/summary.py`)

After initialization, `write_summaries` generates four files:

| File                                           | Content                                    |
|------------------------------------------------|--------------------------------------------|
| `.engram/summaries/project-summary.md`         | Repo path, doc count, archive stats, discovered documents list |
| `.engram/summaries/commands.md`                | Promoted bash commands in fenced code blocks |
| `.engram/summaries/preferences.md`             | Promoted user preferences as a bullet list  |
| `.engram/imports/claude-seed-manifest.json`    | Machine-readable import statistics          |

These summaries serve as both human-readable documentation and the source for
the project summary returned by `build_context`.

### 4.8 Configuration (`src/engram/config.py`)

All path resolution is centralized in `config.py`. No module computes state
paths independently.

| Function                     | Returns                                    |
|------------------------------|--------------------------------------------|
| `global_state_dir()`         | `$ENGRAM_HOME` or `~/.engram`              |
| `global_config_path()`       | `~/.engram/config.yaml`                    |
| `db_path()`                  | `~/.engram/engram.db`                      |
| `global_rules_dir()`         | `~/.engram/rules/`                         |
| `global_rule_path()`         | `~/.engram/rules/global.md`                |
| `claude_projects_dir()`      | `~/.claude/projects/`                      |
| `repo_state_dir(repo_root)`  | `<repo_root>/.engram/`                     |

`ensure_default_global_config()` creates the full global directory layout and
writes default `config.yaml` and `global.md` files if they do not exist.

`ensure_global_layout()` creates subdirectories:
`logs/`, `cache/`, `projects/`, `locks/`, `backups/`, `rules/`, `rules/agents/`.

### 4.9 Doctor (`src/engram/doctor.py`)

Pre-flight validation that checks:

| Check            | Required | What it verifies                              |
|------------------|----------|-----------------------------------------------|
| `git`            | Yes      | `git` binary on PATH                          |
| `rg`             | Yes      | `rg` (ripgrep) binary on PATH                 |
| `global_state`   | Yes      | `~/.engram` is writable                       |
| `sqlite_path`    | Yes      | Parent directory of `engram.db` is writable   |
| `claude_history`  | No       | `~/.claude/projects/` exists                  |
| `repo_exists`    | Yes*     | Target repo path exists (if `--repo` given)   |
| `repo_access`    | Yes*     | Target repo is a readable directory            |

### 4.10 Models (`src/engram/models.py`)

Pure dataclasses with no behavior, used as structured transfer objects across modules:

- `DoctorCheck` -- name, ok, detail, required
- `DetectedDoc` -- path, title, body, doc_type, source_kind, scope_type, scope_key, metadata
- `ClaudeEvent` -- event_type, role, tool_name, content_text, content_json, timestamp, parent_uuid, uuid
- `ClaudeSession` -- source_path, file_hash, session_id, agent_id, cwd, git_branch, is_sidechain, started_at, ended_at, events
- `ImportResult` -- counters for files_seen, sessions_imported/skipped, events_imported, command/preference counts, plus the promoted lists
- `InitResult` -- repo_root, project_id, docs_indexed, rules_indexed, import_result, summaries_written

---

## 5. Data Model

### Entity-Relationship Diagram

```
+-------------------+       +-------------------+
|    projects       |       |  schema_migrations|
|-------------------|       |-------------------|
| id (PK)           |       | name (PK)         |
| repo_path (UQ)    |       | applied_at        |
| repo_name         |       +-------------------+
| default_branch    |
| language_summary  |
| created_at        |
| updated_at        |
+-------------------+
        |
        | 1
        |
        +--------+-----------+-----------+
        |        |           |           |
        v N      v N         v N         v N
+------------+ +----------+ +----------+ +-----------------+
|   scopes   | | documents| | memory   | | archive_sessions|
|------------| |----------| | _items   | |-----------------|
| id (PK)    | | id (PK)  | |----------| | id (PK)         |
| project_id | | project_id| | id (PK) | | project_id      |
| scope_type | | scope_id | | project_id| | source          |
| scope_key  | | doc_type | | scope_id | | session_id      |
| parent_    | | path(UQ) | | kind     | | agent_id        |
|  scope_id  | | title    | | title    | | cwd             |
| priority   | | body     | | body     | | git_branch      |
| created_at | | metadata | | source_key| | is_sidechain   |
+------------+ | _json    | | confidence| | started_at     |
     |         | created_at| | importance| | ended_at       |
     | 1       | updated_at| | stability | | source_path    |
     |         +----------+ | status    | | file_hash      |
     v N                    | valid_from| | created_at     |
+----------+               | valid_to  | +-----------------+
|  rules   |               | created_at|        |
|----------|               | updated_at|        | 1
| id (PK)  |               +----------+        |
| scope_id |                    |               v N
| source_  |                    | 1      +------------------+
|  path    |                    |        | archive_events   |
| source_  |                    v N      |------------------|
|  kind    |          +-----------------+| id (PK)          |
| title    |          | memory_         || archive_session_id|
| body     |          |  provenance     || event_index      |
| normal-  |          |-----------------|  | event_type     |
|  ized_   |          | id (PK)         || role             |
|  body    |          | memory_id       || tool_name        |
| hash     |          | archive_        || content_text     |
| active   |          |  session_id     || content_json     |
| updated  |          | archive_        || timestamp        |
|  _at     |          |  event_id       || parent_uuid      |
+----------+          | document_id     || uuid             |
                      | source_excerpt  || created_at       |
                      | created_at      |+------------------+
                      +-----------------+
```

### Unique Constraints

| Table              | Unique Columns                                |
|--------------------|-----------------------------------------------|
| `projects`         | `repo_path`                                   |
| `scopes`           | `(project_id, scope_type, scope_key)`         |
| `rules`            | `(scope_id, source_path)`                     |
| `documents`        | `(project_id, path)`                          |
| `archive_sessions` | `(source_path, file_hash)`                    |
| `memory_items`     | `(project_id, scope_id, kind, source_key)`    |

### Indexes

```sql
idx_scopes_project_type        ON scopes(project_id, scope_type, priority)
idx_rules_scope_active         ON rules(scope_id, active)
idx_documents_project_type     ON documents(project_id, doc_type)
idx_archive_sessions_project   ON archive_sessions(project_id, started_at)
idx_archive_events_session     ON archive_events(archive_session_id, event_index)
idx_memory_items_project_kind  ON memory_items(project_id, kind, status)
idx_memory_provenance_memory   ON memory_provenance(memory_id)
```

### Scope Hierarchy

Scopes form a priority-ordered hierarchy. When resolving rules, all matching
scopes are included, ordered by ascending priority (broadest first). The
`parent_scope_id` column enables tree traversal, though current resolution
uses flat enumeration.

```
global (10)
  +-- repo (20)
        +-- path (30)
        +-- agent (40)
        +-- branch (50)
        +-- session (60)
  +-- global_agent (35)
```

---

## 6. MCP Protocol

### Overview

Engram implements an MCP server that communicates via stdio. It is designed to be
launched as a subprocess by AI coding clients (Claude Code, etc.) as configured
in `.mcp.json`:

```json
{
  "mcpServers": {
    "engram": {
      "command": "python3",
      "args": ["-u", "-m", "engram", "mcp"],
      "env": {
        "PYTHONPATH": "/path/to/agent-memory/src"
      }
    }
  }
}
```

### Transport

**Input:** The server reads from `sys.stdin.buffer` line by line. Two framing
modes are supported:

1. **Newline-delimited JSON (primary):** If a line starts with `{`, it is
   parsed directly as a JSON-RPC message.
2. **Content-Length framing (fallback):** If a line contains a `:` header
   separator, the server reads HTTP-style headers until a blank line, then
   reads exactly `Content-Length` bytes as the message body.

**Output:** Each response is written as a single JSON object followed by `\n`,
flushed immediately. No Content-Length headers on output.

### JSON-RPC 2.0 Messages

All messages conform to JSON-RPC 2.0. Requests include `"jsonrpc": "2.0"`,
a `method`, optional `params`, and an `id` for requests expecting responses.

**Initialization handshake:**

```
Client -> { "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": { "protocolVersion": "2024-11-05" } }

Server -> { "jsonrpc": "2.0", "id": 1, "result": {
              "protocolVersion": "2024-11-05",
              "capabilities": { "tools": {} },
              "serverInfo": { "name": "engram", "version": "0.1.0" }
            }}

Client -> { "jsonrpc": "2.0", "method": "notifications/initialized" }
```

### Tool Catalog

Returned by `tools/list`:

| Tool Name       | Required Params   | Optional Params                                 | Description                                                   |
|-----------------|-------------------|-------------------------------------------------|---------------------------------------------------------------|
| `doctor`        | (none)            | (none)                                          | Validate local runtime requirements                           |
| `project_show`  | `repo`            | (none)                                          | Show project stats and summaries                              |
| `project_sync`  | `repo`            | `seed_claude`, `include_subagents`, `since_days` | Initialize or refresh project state                           |
| `rules_show`    | `repo`            | `path`, `agent`, `branch`, `session`            | Resolve deterministic scoped rules                            |
| `memory_search` | `repo`, `query`   | `kind`, `limit` (1-50)                          | Search distilled repo memory                                  |
| `memory_list`   | `repo`            | `kind`                                          | List stored project memory                                    |
| `memory_store`  | `repo`, `kind`, `title`, `body` | `source_context`                      | Store a project memory item                                   |
| `memory_delete` | `repo`, `memory_id` | (none)                                        | Delete a stored project memory item                           |
| `document_search` | `repo`, `query` | `doc_type`, `limit` (1-50)                     | Search indexed project documents                              |
| `context_build` | `repo`, `query`   | `path`, `agent`, `branch`, `session`, `memory_limit` (1-20), `doc_limit` (1-20) | Build full context bundle |

### Tool Call / Response Format

```
Client -> { "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": { "name": "memory_search",
                        "arguments": { "repo": "/path/to/repo",
                                       "query": "pytest",
                                       "kind": "command" } } }

Server -> { "jsonrpc": "2.0", "id": 5, "result": {
              "content": [
                { "type": "text",
                  "text": "{ ... JSON payload ... }" }
              ]
            }}
```

Tool results are always wrapped in MCP's content-block format. Errors from tool
execution are returned as successful JSON-RPC responses with `"isError": true`
in the result, not as JSON-RPC error responses. Only unknown methods produce
JSON-RPC error frames (code `-32601`).

---

## 7. State Layout

### Global State (`~/.engram/`)

```
~/.engram/
  config.yaml              # Database backend, tool paths, model config, defaults
  engram.db                # SQLite database (WAL mode)
  engram.db-wal            # WAL file (created by SQLite)
  engram.db-shm            # Shared memory file (created by SQLite)
  rules/
    global.md              # Cross-repo rules applied to all projects
    agents/
      <agent-name>.md      # Global per-agent rules (e.g., reviewer.md)
  logs/                    # Reserved for future logging
  cache/                   # Reserved for future caching
  projects/                # Reserved for future per-project metadata
  locks/                   # Reserved for future lock files
  backups/                 # Reserved for future database backups
```

The `ENGRAM_HOME` environment variable overrides the default `~/.engram` location.

### Per-Repo State (`<repo_root>/.engram/`)

```
<repo_root>/.engram/
  project.yaml             # Project name, repo root, seed configuration
  rules/
    repo.md                # Repository-level rules
    agents/
      <agent-name>.md      # Per-agent rules scoped to this repo
    paths/
      <encoded-path>.md    # Per-path rules (/ encoded as __)
    branches/
      <branch-name>.md     # Per-branch rules
    sessions/              # Reserved for per-session rules
  summaries/
    project-summary.md     # Generated project overview
    commands.md            # Promoted bash commands
    preferences.md         # Promoted user preferences
  imports/
    claude-seed-manifest.json  # Import statistics
  cache/                   # Reserved for future caching
```

### Claude History (read-only source)

```
~/.claude/projects/
  <project-hash>/
    <session-id>.jsonl     # One line per event, newline-delimited JSON
```

These files are read during `engram init --seed-claude` but never modified.

---

## Appendix: Module Dependency Graph

```
cli.py
  +-- project.py
  |     +-- config.py
  |     +-- claude.py
  |     |     +-- config.py
  |     |     +-- db.py
  |     |     +-- models.py
  |     +-- db.py
  |     +-- models.py
  |     +-- repoindex.py
  |     |     +-- config.py
  |     |     +-- models.py
  |     +-- rules.py
  |     +-- summary.py
  |           +-- models.py
  +-- query.py
  |     +-- config.py
  |     +-- db.py
  |     +-- rules.py
  +-- db.py
  +-- doctor.py
  |     +-- config.py
  |     +-- models.py
  +-- mcp.py
        +-- config.py
        +-- db.py
        +-- doctor.py
        +-- query.py
```
