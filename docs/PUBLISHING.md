# Publishing

`codemem` currently publishes as a Python zipapp release artifact, not a native compiled binary.

Target machine requirements:

- `python3` 3.9+
- `git`
- `rg`

## GitHub Releases

The repo already contains a release workflow:

- [.github/workflows/release.yml](/Users/gauravyadav/exp/agent-memory/.github/workflows/release.yml)

Tag-based release flow:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_dist.py
git tag v0.1.0
git push origin v0.1.0
```

The workflow will:

- run unit tests
- build `dist/codemem-<version>.pyz`
- build `dist/codemem-<version>.tar.gz`
- publish everything in `dist/` to the GitHub release

Local artifacts are built with:

- [scripts/build_dist.py](/Users/gauravyadav/exp/agent-memory/scripts/build_dist.py)

## Direct Install From Release

Once a GitHub release exists, another machine can install directly:

```bash
curl -L -o codemem https://github.com/<owner>/<repo>/releases/download/v0.1.0/codemem-0.1.0.pyz
chmod +x codemem
mv codemem ~/.local/bin/codemem
codemem doctor
```

## Homebrew

The easiest Homebrew path is a custom tap, not `homebrew-core`.

Recommended structure:

- app repo: `github.com/<owner>/codemem`
- tap repo: `github.com/<owner>/homebrew-tools`
- formula path in tap repo: `Formula/codemem.rb`

Generate the formula from the current release artifact:

```bash
python3 scripts/build_dist.py
python3 scripts/render_homebrew_formula.py --owner <owner> --repo <repo>
```

This writes:

- `dist/codemem.rb`

Copy that into the tap repo as `Formula/codemem.rb`.

Then users can install with:

```bash
brew tap <owner>/tools
brew install codemem
```

The formula uses Homebrew's `python@3.11` and installs a small wrapper script that runs the released `.pyz`.

## What Homebrew Actually Requires

For this project, Homebrew adds these requirements:

1. a stable public GitHub release URL for `codemem-<version>.pyz`
2. a SHA256 for that exact artifact
3. a tap repo containing `Formula/codemem.rb`
4. one manual or automated formula update per release

That means GitHub Releases are almost done already; Homebrew is one extra repository and one formula update step.

## Later Upgrade Path

If `codemem` is rewritten in Go later, Homebrew gets simpler:

- no Python dependency
- native per-platform binaries
- smaller install wrapper

Until then, the zipapp release is the lowest-friction portable distribution model.
