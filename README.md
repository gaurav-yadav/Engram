# codemem

`codemem` is a local-first coding memory tool for deterministic coding rules, Claude chat import, distilled repo memory, and assistant-facing retrieval.

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
cd /Users/gauravyadav/exp/agent-memory
PYTHONPATH=src python3 -m codemem doctor
PYTHONPATH=src python3 -m codemem init /path/to/repo --seed-claude --include-subagents --since 180d
PYTHONPATH=src python3 -m codemem project show /path/to/repo
PYTHONPATH=src python3 -m codemem rules show /path/to/repo --path src/app.py --agent reviewer
PYTHONPATH=src python3 -m codemem memory search /path/to/repo pytest --kind command
PYTHONPATH=src python3 -m codemem context /path/to/repo "failing tests in ingestion"
PYTHONPATH=src python3 -m codemem mcp
```

## Running On Another Machine

Target machine requirements:

- `python3` 3.9+
- `git`
- `rg`

The simplest install path is the release artifact:

```bash
curl -L -o codemem.pyz https://github.com/<owner>/<repo>/releases/download/v0.1.0/codemem-0.1.0.pyz
install -m 755 codemem.pyz ~/.local/bin/codemem
codemem doctor
```

If you are copying artifacts manually instead of publishing them, use:

```bash
./scripts/install_artifact.sh dist/codemem-0.1.0.pyz
```

The `.pyz` artifact is a Python zipapp, so it is a single executable file but still uses the target machine's Python runtime.

## Publishing

Build release artifacts locally:

```bash
python3 scripts/build_dist.py
ls dist/
```

This produces:

- `codemem-<version>.pyz`
- `codemem-<version>.tar.gz`
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

- global state under `~/.codemem`
- per-repo state under `/repo/.codemem`

Global cross-repo rules live at:

- `~/.codemem/rules/global.md`

Per-repo rules live at:

- `/repo/.codemem/rules/repo.md`
- `/repo/.codemem/rules/agents/*.md`
- `/repo/.codemem/rules/paths/*.md`
- `/repo/.codemem/rules/branches/*.md`

If the default home directory is not writable, set `CODEMEM_HOME`:

```bash
CODEMEM_HOME=/tmp/codemem PYTHONPATH=src python3 -m codemem doctor
```

## Notes

- The current implementation uses SQLite and stdlib only.
- Distribution is currently based on a Python zipapp; there is no compiled native binary yet.
- Claude seed import archives raw sessions and promotes repeated commands plus explicit user preferences.
- PostgreSQL, semantic retrieval, and richer memory extraction remain deferred until the local workflow is stable.

## Publishing

Detailed release and Homebrew notes live in [docs/PUBLISHING.md](/Users/gauravyadav/exp/agent-memory/docs/PUBLISHING.md).
