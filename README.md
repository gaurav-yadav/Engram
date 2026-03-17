# Engram

Engram is a local-first coding memory tool for deterministic coding rules, Claude chat import, distilled repo memory, and assistant-facing retrieval.

Current scope:

- `doctor` validates local runtime requirements
- `init` bootstraps a repo, creates local state, indexes docs and rules, optionally imports Claude chats, and writes summaries
- `project show` reports project stats and summaries
- `rules show` resolves deterministic scoped rules
- `memory search` searches distilled memory with provenance
- `context` assembles summary, rules, memory, and docs for a coding query
- `mcp` serves the same retrieval surface over stdio JSON-RPC
- `serve` exposes a minimal local HTTP skeleton

## Requirements

- Python 3.9+
- `git`
- `rg`

## Quick start

```bash
cd Engram
PYTHONPATH=src python3 -m engram doctor
PYTHONPATH=src python3 -m engram init /path/to/repo --seed-claude --include-subagents --since 180d
PYTHONPATH=src python3 -m engram project show /path/to/repo
PYTHONPATH=src python3 -m engram rules show /path/to/repo --path src/app.py --agent reviewer
PYTHONPATH=src python3 -m engram memory search /path/to/repo pytest --kind command
PYTHONPATH=src python3 -m engram context /path/to/repo "failing tests in ingestion"
PYTHONPATH=src python3 -m engram mcp
```

## Running On Another Machine

Target machine requirements:

- `python3` 3.9+
- `git`
- `rg`

The simplest install path is the release installer:

```bash
curl -fsSL https://raw.githubusercontent.com/gaurav-yadav/Engram/main/scripts/install_release.sh | sh
engram doctor
```

To install a specific release, pass the tag:

```bash
curl -fsSL https://raw.githubusercontent.com/gaurav-yadav/Engram/main/scripts/install_release.sh | sh -s -- v0.1.0
engram doctor
```

If you are copying artifacts manually instead of publishing them, use:

```bash
sh ./scripts/install_artifact.sh dist/engram-0.1.0.pyz
```

The `.pyz` artifact is a Python zipapp, so it is a single executable file but still uses the target machine's Python runtime.

## Publishing

Build release artifacts locally:

```bash
python3 scripts/build_dist.py
ls dist/
```

This produces:

- `engram-<version>.pyz`
- `engram-<version>.tar.gz`
- `checksums.txt`

GitHub release publishing is wired in [release.yml](.github/workflows/release.yml). Pushing a tag like `v0.1.0` will:

- run the unit tests
- build the artifacts
- attach everything in `dist/` to the GitHub release

Manual release flow:

```bash
python3 scripts/build_dist.py
git tag v0.1.0
git push origin v0.1.0
```

This creates:

- global state under `~/.engram`
- per-repo state under `/repo/.engram`

Global cross-repo rules live at:

- `~/.engram/rules/global.md`

Per-repo rules live at:

- `/repo/.engram/rules/repo.md`
- `/repo/.engram/rules/agents/*.md`
- `/repo/.engram/rules/paths/*.md`
- `/repo/.engram/rules/branches/*.md`

If the default home directory is not writable, set `ENGRAM_HOME`:

```bash
ENGRAM_HOME=/tmp/engram PYTHONPATH=src python3 -m engram doctor
```

## Notes

- The current implementation uses SQLite and stdlib only.
- Distribution is currently based on a Python zipapp; there is no compiled native binary yet.
- Claude seed import archives raw sessions and promotes repeated commands plus explicit user preferences.
- PostgreSQL, semantic retrieval, and richer memory extraction remain deferred until the local workflow is stable.

## Publishing Notes

Detailed release and Homebrew notes live in [docs/PUBLISHING.md](docs/PUBLISHING.md).
