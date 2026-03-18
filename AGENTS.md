# Repository Guidelines

## Project Structure & Module Organization

The repository and runtime package are both named `Engram`. Core application code lives in `src/engram/`. Key modules are `cli.py` for commands, `mcp.py` for the stdio server, `db.py` for SQLite schema and queries, `claude.py` for chat import, and `query.py` for retrieval assembly. Tests live in `tests/` and mirror the runtime modules with `test_*.py` files. Release and install helpers live in `scripts/`, CI lives in `.github/workflows/`, and longer publishing notes live in `docs/`. Do not commit local runtime state from `.engram/`, `.engram-home/`, or built artifacts in `dist/`.

## Build, Test, and Development Commands

Run commands from the repo root:

```bash
PYTHONPATH=src python3 -m engram doctor
PYTHONPATH=src python3 -m engram init /path/to/repo --seed-claude
PYTHONPATH=src python3 -m engram sync /path/to/repo
PYTHONPATH=src python3 -m engram auto-init /path/to/repo
PYTHONPATH=src python3 -m engram setup-hooks
PYTHONPATH=src python3 -m engram docs search /path/to/repo onboarding
PYTHONPATH=src python3 -m engram memory store note "Sync Workflow" "Run engram sync before coding sessions." --repo /path/to/repo
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/build_dist.py
python3 scripts/render_homebrew_formula.py --owner <owner> --repo <repo>
```

`doctor` validates local dependencies, `init` bootstraps repo memory, `sync` refreshes indexed state, `auto-init` provides idempotent first-run setup, `setup-hooks` installs the Claude SessionStart hook, `docs search` exercises direct document retrieval, `memory store` exercises write-side memory management, the `unittest` command is the main test entrypoint, and `build_dist.py` creates release artifacts. Most read-oriented commands can infer the repo from the current working tree when run inside a repository.

## Coding Style & Naming Conventions

Use 4-space indentation and LF line endings as defined in `.editorconfig`. Keep Python stdlib-only unless there is a strong reason to add a dependency. Follow existing naming: `snake_case` for functions and modules, `CapWords` for classes, and short, focused dataclasses for shared models. Prefer type hints on public functions and keep CLI output concise and deterministic.

## Testing Guidelines

Use `unittest` with files named `test_<module>.py`. Add or update tests for any change that touches CLI behavior, database migrations, rule resolution, document retrieval, initialization flow, MCP tools, memory management, or Claude import logic. There is no formal coverage gate yet, but new logic should ship with a direct test and at least one realistic path-level assertion where practical.

## Commit & Pull Request Guidelines

This repository has no commit history yet, so start with short imperative commit messages such as `Add rule search command` or `Wire release artifacts`. Keep one logical change per commit. Pull requests should include: what changed, how it was tested, any new commands or config paths, and sample output when CLI or MCP behavior changes.

## Security & Configuration Tips

Treat Claude chat archives and generated SQLite state as local-only data. Use `ENGRAM_HOME` when you need an isolated state directory during development or tests.
