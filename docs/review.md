# Code Review: Engram Codebase

**Date:** 2026-03-17
**Scope:** Current Python codebase under `src/engram/`, supporting docs, and tests.

---

## Summary

Engram is in a good state for an early repo: the architecture is small, coherent, stdlib-only, and easy to extend. The recent usability work improved the product surface materially by adding `auto-init`, `setup-hooks`, repo inference for read-oriented commands, direct document search, and better MCP behavior for uninitialized repos.

The main remaining issues are operational hardening rather than architectural confusion: cleanup on init failure, better hook robustness, stronger validation around config and scope types, and more transport-level test coverage.

---

## What's Good

- **The module boundaries are still clean.** CLI and MCP stay thin and delegate into `query.py`, `project.py`, and `db.py`.
- **The zero-runtime-dependency constraint is intact.** `pyproject.toml` still declares `dependencies = []`.
- **Initialization and retrieval are product-shaped now.** `auto-init`, `setup-hooks`, repo inference, and `docs search` make the tool much easier to activate.
- **The MCP surface is meaningfully better.** `ProjectNotInitializedError` is translated into a structured `status: "not_initialized"` response instead of a generic tool failure.
- **The retrieval model is coherent.** Rules, memory, and indexed documents are all available through both CLI and MCP.
- **Tests now cover the new usability slice.** There is direct coverage for `auto-init`, hook setup idempotency, inferred repo behavior, and MCP document search.

---

## Findings

### 1. `initialize_project()` does not guarantee database cleanup on failure

- **File:** `src/engram/project.py`
- **Why it matters:** `Database` is opened and closed manually. If anything between those points raises, the connection is leaked until process exit.
- **Current state:** This is still real. `db.close()` runs only on the success path.
- **Suggested fix:** Wrap the body in `try/finally`, or add context-manager support to `Database`.

### 2. The generated auto-init hook assumes `engram` is on `PATH`

- **File:** `src/engram/cli.py`
- **Why it matters:** The generated shell script runs `nohup engram auto-init "$REPO" ...`. That is correct for installed releases, but it fails for development flows that use `PYTHONPATH=src python3 -m engram`.
- **Current state:** Still real.
- **Suggested fix:** Generate a fallback wrapper that prefers `engram` but can fall back to `python3 -m engram`, or make `setup-hooks` choose the invocation mode explicitly.

### 3. `setup-hooks` will fail on malformed `~/.claude/settings.json`

- **File:** `src/engram/cli.py`
- **Why it matters:** `json.loads()` is called directly on the existing settings file. Invalid JSON causes the command to fail without recovery or backup.
- **Current state:** Still real.
- **Suggested fix:** Catch `json.JSONDecodeError`, print a clear message, and either abort safely or write through a backup-and-replace flow.

### 4. `doctor.py` performs duplicate writable-dir probe writes

- **File:** `src/engram/doctor.py`
- **Why it matters:** `_is_writable_dir()` is called twice for `global_state` and twice for `sqlite_path`. That doubles the write probe work and makes the code noisier than needed.
- **Current state:** Still real.
- **Suggested fix:** Evaluate `_is_writable_dir()` once per path and reuse the result for both `ok` and `detail`.

### 5. Unknown scope types are still silently accepted

- **Files:** `src/engram/rules.py`, `src/engram/db.py`
- **Why it matters:** `scope_priority()` returns `0` for unknown scope names, and `ensure_scope()` accepts any string. A typo in a scope type can create data that is stored but never resolved.
- **Current state:** Still real.
- **Suggested fix:** Validate scope types against `SCOPE_ORDER` before inserting or prioritizing them.

### 6. The architecture doc still omits `document_search`

- **File:** `docs/architecture.md`
- **Why it matters:** The current MCP implementation exposes `document_search`, but the architecture document’s tool catalog still lists only `doctor`, `project_show`, `rules_show`, `memory_search`, and `context_build`.
- **Current state:** Still real.
- **Suggested fix:** Update the tool catalog and the “Exposed tools” section in `docs/architecture.md`.

### 7. CLI input validation is serviceable but still rough in edge cases

- **File:** `src/engram/cli.py`
- **Why it matters:** `_parse_since()` still raises the raw `int()` conversion error for malformed values, and some invalid-input messages remain more developer-oriented than user-oriented.
- **Current state:** Still real.
- **Suggested fix:** Turn parsing failures into explicit CLI-friendly messages, especially for `--since`.

---

## Test Coverage Gaps

- **No MCP transport-level test yet.** `tests/test_mcp.py` covers tool behavior, not the full stdio JSON-RPC loop in `run_stdio_server()`.
- **No HTTP server test yet.** `_cmd_serve` and `_ServeHandler` still have no direct coverage.
- **No direct `build_context()` test yet.** The highest-level retrieval path is still covered only indirectly.
- **No invalid-settings test for `setup-hooks`.** The failure mode described above is not exercised.
- **No failure-path test for `initialize_project()`.** There is no regression test around DB cleanup if import or indexing fails mid-init.

---

## Resolved Since The Previous Draft

These older review claims are no longer accurate and should not be carried forward:

- The repo **does** ignore `.engram/` in `.gitignore`.
- The README **does** document direct document search in the CLI surface.
- There **are now** dedicated tests for the recent usability additions (`test_cli.py`, `test_mcp.py`).

---

## Recommended Next Steps

1. Harden initialization cleanup and hook generation.
2. Clean up `doctor.py` and CLI validation rough edges.
3. Add MCP transport tests and HTTP endpoint tests.
4. Update `docs/architecture.md` so the docs match the current product surface.
