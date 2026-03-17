from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
SRC = ROOT / "src"


def _version() -> str:
    init_path = SRC / "codemem" / "__init__.py"
    for line in init_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__ = "):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("could not determine version")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _formula_text(owner: str, repo: str, version: str, sha256: str) -> str:
    url = f"https://github.com/{owner}/{repo}/releases/download/v{version}/codemem-{version}.pyz"
    return "\n".join(
        [
            "class Codemem < Formula",
            '  desc "Local-first coding memory for repo-scoped rules and Claude archive import"',
            f'  homepage "https://github.com/{owner}/{repo}"',
            f'  url "{url}"',
            f'  version "{version}"',
            f'  sha256 "{sha256}"',
            '  license "MIT"',
            "",
            '  depends_on "python@3.11"',
            "",
            "  def install",
            '    libexec.install "codemem-#{version}.pyz" => "codemem.pyz"',
            '    (bin/"codemem").write <<~EOS',
            '      #!/bin/bash',
            '      exec "#{Formula["python@3.11"].opt_bin}/python3" "#{libexec}/codemem.pyz" "$@"',
            "    EOS",
            "  end",
            "",
            "  test do",
            '    output = shell_output("#{bin}/codemem --version")',
            '    assert_match "codemem #{version}", output',
            "  end",
            "end",
            "",
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Homebrew formula for codemem")
    parser.add_argument("--owner", required=True, help="GitHub owner or org")
    parser.add_argument("--repo", required=True, help="GitHub repository name")
    parser.add_argument(
        "--artifact",
        default=None,
        help="Path to the .pyz artifact; defaults to dist/codemem-<version>.pyz",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path; defaults to dist/codemem.rb",
    )
    args = parser.parse_args(argv)

    version = _version()
    artifact = Path(args.artifact).resolve() if args.artifact else DIST / f"codemem-{version}.pyz"
    if not artifact.exists():
        raise FileNotFoundError(f"artifact not found: {artifact}")

    formula = _formula_text(args.owner, args.repo, version, _sha256(artifact))
    output = Path(args.output).resolve() if args.output else DIST / "codemem.rb"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(formula, encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
