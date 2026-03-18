# Design: Auto-Init for Engram Projects

## Problem

Users must manually run `engram init /path/to/repo --seed-claude` before Engram's
MCP tools return useful results. Every MCP tool handler calls
`_load_project_or_raise` (in `src/engram/query.py`), which throws a hard
`ValueError("project is not initialized: ...")` if the repo has never been
through `initialize_project`. That error surfaces as an opaque MCP `isError`
response to the calling agent.

This means:

1. The first interaction with any Engram tool in a new repo is always a failure.
2. The user has to know the incantation (`engram init <path> --seed-claude`),
   leave their agent session, run it, and return.
3. In practice, most repos stay uninitialized. Engram delivers zero value until
   someone opts in per-repo.

Auto-init eliminates this friction: Engram should initialize a project the
moment a session touches it, with no user intervention.

---

## Design

### Detection Strategy

A single filesystem `stat()` on the marker file `.engram/project.yaml` is the
definitive check for whether a repo has been initialized.

```
repo_root / ".engram" / "project.yaml"
```

`ensure_repo_layout` in `src/engram/repoindex.py` writes this file during
`initialize_project`. It is always present after a successful init and never
present before one.

**Why a filesystem marker instead of a DB lookup:**

- The DB lives at `~/.engram/engram.db`, not inside the repo. A DB check
  requires constructing a `Database`, running `migrate()`, and executing a
  SELECT. That is ~30 ms of Python startup + SQLite overhead -- unacceptable
  for a hook that fires on every session start.
- The `project.yaml` stat is a single syscall. From a shell script it is
  `[ -f "$repo/.engram/project.yaml" ]`, which costs <1 ms.
- The marker file already exists in the current design; no new artifact is
  introduced.
- Edge case: a user could delete `.engram/` but leave the DB row. This is
  acceptable -- re-running init is idempotent (`get_or_create_project` in
  `src/engram/db.py` upserts, and `ensure_repo_layout` is mkdir-safe).

### New CLI Subcommand: `engram auto-init`

Add a new subcommand purpose-built for unattended invocation.

```
engram auto-init [repo]
```

Behavior:

| Condition | Action | Exit code |
|---|---|---|
| `.engram/project.yaml` exists | Print nothing, exit immediately | 0 |
| Marker absent, repo is a valid directory | Run `initialize_project(seed_claude=True, include_subagents=True, since_days=180)` | 0 |
| Marker absent, repo is not a directory | Log warning, exit | 0 |
| Any exception during init | Log to `~/.engram/logs/auto-init.log`, exit | 0 |

Key properties:

- **Never raises.** Every code path returns 0. This is critical because the
  command will be called from hooks where a non-zero exit can abort the parent
  process.
- **Defaults to cwd.** If `repo` is omitted, uses `Path.cwd()`. This matches
  how Claude Code sessions work (cwd is the project root).
- **Idempotent.** Safe to call repeatedly. The fast-path (marker exists) is a
  single `Path.exists()` before any imports beyond `pathlib`.
- **Logs, never prints.** Output goes to `~/.engram/logs/auto-init.log` with
  timestamps, not stdout/stderr. This avoids polluting agent sessions.

Implementation in `src/engram/cli.py`:

```python
def _cmd_auto_init(args: argparse.Namespace) -> int:
    import logging

    repo = Path(args.repo).resolve() if args.repo else Path.cwd().resolve()
    marker = repo / ".engram" / "project.yaml"

    log_dir = config.global_state_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "auto-init.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if marker.exists():
        logging.debug("already initialized: %s", repo)
        return 0

    if not repo.is_dir():
        logging.warning("not a directory, skipping: %s", repo)
        return 0

    try:
        result = initialize_project(
            repo_root=repo,
            seed_claude=True,
            include_subagents=True,
            since_days=180,
        )
        logging.info(
            "auto-initialized %s (project_id=%d, docs=%d, rules=%d, sessions=%d)",
            repo, result.project_id, result.docs_indexed,
            result.rules_indexed, result.import_result.sessions_imported,
        )
    except Exception:
        logging.exception("auto-init failed for %s", repo)

    return 0
```

Parser registration:

```python
auto_init = subparsers.add_parser("auto-init", help="Idempotent init for hooks")
auto_init.add_argument("repo", nargs="?", default=None, help="Repository root (default: cwd)")
auto_init.set_defaults(func=_cmd_auto_init)
```

### Claude Code Integration

The goal is to run `engram auto-init` at the start of every Claude Code session
with minimal latency impact on the common case (already initialized).

#### Shell Wrapper Script

A thin shell script handles the fast-path entirely in the shell, avoiding
Python startup for the 99% case where the project is already initialized.

`~/.engram/bin/engram-auto-init.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-$(pwd)}"
MARKER="$REPO/.engram/project.yaml"

# Fast path: already initialized (single stat syscall)
[ -f "$MARKER" ] && exit 0

# Slow path: fork init to background so the session is not blocked
nohup python3 -m engram auto-init "$REPO" >> /dev/null 2>&1 &
discard=$!

exit 0
```

Properties:

- The `[ -f ... ]` test is <1 ms. No Python process is spawned for initialized
  repos.
- For uninitialized repos, `nohup ... &` forks the init to the background. The
  session starts immediately; init completes asynchronously. The MCP tools will
  return a helpful "not yet initialized" message (see below) until init
  finishes.
- The script itself always exits 0.

#### SessionStart Hook Configuration

Claude Code supports a `hooks` configuration in `~/.claude/settings.json`.
The `SessionStart` hook fires once when a new session begins.

```jsonc
// ~/.claude/settings.json (relevant fragment)
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "command": "bash ~/.engram/bin/engram-auto-init.sh"
      }
    ]
  }
}
```

The empty `matcher` means the hook fires for all projects. The shell script
internally decides whether init is needed.

### Optional: `engram setup-hooks` Command

A convenience command that writes the hook configuration into the user's
Claude Code settings.

```
engram setup-hooks
```

Behavior:

1. Ensure `~/.engram/bin/engram-auto-init.sh` exists and is executable.
   Write the shell script content if missing.
2. Read `~/.claude/settings.json` (create with `{}` if absent).
3. Parse as JSON. Merge the `hooks.SessionStart` entry, avoiding duplicates
   (match on command string).
4. Write back with `indent=2`.
5. Print confirmation to stdout.

Implementation in `src/engram/cli.py`:

```python
def _cmd_setup_hooks(args: argparse.Namespace) -> int:
    import stat

    # 1. Write shell wrapper
    bin_dir = config.global_state_dir() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "engram-auto-init.sh"
    script.write_text(AUTOINIT_SHELL_SCRIPT, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    # 2. Read or create settings.json
    settings_path = config.home_dir() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings = {}

    # 3. Merge hook entry
    hooks = settings.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    hook_command = f"bash {script}"
    if not any(h.get("command") == hook_command for h in session_start):
        session_start.append({"matcher": "", "command": hook_command})

    # 4. Write back
    settings_path.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Wrote hook script: {script}")
    print(f"Updated settings:  {settings_path}")
    return 0
```

### MCP Error Handling Improvement

Currently, when a tool is called against an uninitialized project,
`_load_project_or_raise` in `src/engram/query.py` raises:

```
ValueError("project is not initialized: /path/to/repo")
```

This bubbles through `mcp.py` line 298 as a generic error:

```python
except Exception as exc:
    _write_message(_success(message_id, _tool_result({"error": str(exc)}, is_error=True)))
```

The agent sees `"project is not initialized"` with no guidance.

**Change:** Catch the "not initialized" case explicitly in `_with_db` and
return a structured, actionable response instead of an error.

Updated `_with_db` in `src/engram/mcp.py`:

```python
class ProjectNotInitializedError(ValueError):
    """Raised when a tool is called against an uninitialized project."""
    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        super().__init__(f"project is not initialized: {repo_root}")


def _with_db(handler, arguments):
    db = Database(config.db_path())
    db.migrate()
    try:
        return handler(db, arguments)
    except ProjectNotInitializedError as exc:
        repo = exc.repo_root
        marker = Path(repo) / ".engram" / "project.yaml"
        if not marker.exists():
            hint = (
                f"Project '{repo}' has not been initialized. "
                "Auto-init may be in progress if this is the first session. "
                "Try again in a moment, or run: engram init " + repo + " --seed-claude"
            )
        else:
            hint = (
                f"Project '{repo}' marker exists but DB record is missing. "
                "This can happen after a database reset. "
                "Run: engram init " + repo + " --seed-claude"
            )
        return {"status": "not_initialized", "message": hint}
    finally:
        db.close()
```

Correspondingly, update `_load_project_or_raise` in `src/engram/query.py` to
raise the new exception type so `_with_db` can catch it distinctly:

```python
from engram.mcp import ProjectNotInitializedError

def _load_project_or_raise(db: Database, repo_root: Path) -> sqlite3.Row:
    row = db.get_project(_repo_root(repo_root))
    if row is None:
        raise ProjectNotInitializedError(str(repo_root))
    return row
```

Note: to avoid a circular import, `ProjectNotInitializedError` should live in
`src/engram/errors.py` (a new, small module) and be imported by both
`query.py` and `mcp.py`.

The MCP response for an uninitialized project becomes a normal (non-error)
tool result with `status: "not_initialized"` and a human-readable message. The
calling agent can decide to retry or inform the user, rather than treating it as
a tool failure.

---

## Implementation Steps

| Step | Files | Description | Effort |
|------|-------|-------------|--------|
| 1 | `src/engram/errors.py` (new) | Create `ProjectNotInitializedError` exception class. | 5 min |
| 2 | `src/engram/query.py` | Import `ProjectNotInitializedError`, replace the `ValueError` in `_load_project_or_raise`. | 5 min |
| 3 | `src/engram/mcp.py` | Catch `ProjectNotInitializedError` in `_with_db`, return structured hint response. | 15 min |
| 4 | `src/engram/cli.py` | Add `_cmd_auto_init` function and parser registration for `auto-init` subcommand. | 20 min |
| 5 | `src/engram/cli.py` | Add `_cmd_setup_hooks` function, `AUTOINIT_SHELL_SCRIPT` constant, and parser registration for `setup-hooks` subcommand. | 20 min |
| 6 | `tests/test_auto_init.py` (new) | Tests for `auto-init`: already-initialized fast path, fresh repo init, non-directory handling, exception swallowing. | 20 min |
| 7 | `tests/test_setup_hooks.py` (new) | Tests for `setup-hooks`: script creation, settings.json creation/merge, idempotency. | 15 min |
| 8 | `tests/test_mcp.py` | Add test for uninitialized-project MCP response (structured hint, not error). | 10 min |
| 9 | Documentation / `engram doctor` | Add a `project_initialized` check to `doctor.py` when `--repo` is passed; mention auto-init in output. | 10 min |

**Total: ~2 hours**

### Ordering and Dependencies

Steps 1-3 (error handling) have no dependency on steps 4-5 (CLI commands) and
can be developed in parallel. Step 6 depends on step 4; step 7 depends on
step 5; step 8 depends on steps 1-3. Step 9 is independent.

Recommended merge order: ship steps 1-3 first (improved error messages are
valuable even without auto-init), then steps 4-8 together, then step 9.
