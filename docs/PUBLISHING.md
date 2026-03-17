# Publishing

Engram currently publishes as a Python zipapp release artifact, not a native compiled binary.

Target machine requirements:

- `python3` 3.9+
- `git`
- `rg`

## GitHub Releases

The repo already contains a release workflow:

- [.github/workflows/release.yml](../.github/workflows/release.yml)

Tag-based release flow:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_dist.py
git tag v0.1.0
git push origin v0.1.0
```

The workflow will:

- run unit tests
- build `dist/engram-<version>.pyz`
- build `dist/engram-<version>.tar.gz`
- publish everything in `dist/` to the GitHub release

Local artifacts are built with:

- [scripts/build_dist.py](../scripts/build_dist.py)

## Direct Install From Release

Once a GitHub release exists, another machine can install with the release installer:

```bash
curl -fsSL https://raw.githubusercontent.com/gaurav-yadav/Engram/main/scripts/install_release.sh | sh
engram doctor
```

To pin a specific release:

```bash
curl -fsSL https://raw.githubusercontent.com/gaurav-yadav/Engram/main/scripts/install_release.sh | sh -s -- v0.1.0
engram doctor
```

Manual artifact install still works:

```bash
curl -L -o engram https://github.com/gaurav-yadav/Engram/releases/download/v0.1.0/engram-0.1.0.pyz
install -m 755 engram ~/.local/bin/engram
engram doctor
```

## Homebrew

The easiest Homebrew path is a custom tap, not `homebrew-core`.

Recommended structure:

- app repo: `github.com/gaurav-yadav/Engram`
- tap repo: `github.com/gaurav-yadav/homebrew-tools`
- formula path in tap repo: `Formula/engram.rb`

Generate the formula from the current release artifact:

```bash
python3 scripts/build_dist.py
python3 scripts/render_homebrew_formula.py --owner <owner> --repo <repo>
```

This writes:

- `dist/engram.rb`

Copy that into the tap repo as `Formula/engram.rb`.

Then users can install with:

```bash
brew tap <owner>/tools
brew install engram
```

The formula uses Homebrew's `python@3.11` and installs a small wrapper script that runs the released `.pyz`.

## What Homebrew Actually Requires

For this project, Homebrew adds these requirements:

1. a stable public GitHub release URL for `engram-<version>.pyz`
2. a SHA256 for that exact artifact
3. a tap repo containing `Formula/engram.rb`
4. one manual or automated formula update per release

That means GitHub Releases are almost done already; Homebrew is one extra repository and one formula update step.

## Later Upgrade Path

If `engram` is rewritten in Go later, Homebrew gets simpler:

- no Python dependency
- native per-platform binaries
- smaller install wrapper

Until then, the zipapp release is the lowest-friction portable distribution model.
