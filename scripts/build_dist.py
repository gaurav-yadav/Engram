from __future__ import annotations

import argparse
import hashlib
import tarfile
import zipapp
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DIST = ROOT / "dist"


def _version() -> str:
    init_path = SRC / "engram" / "__init__.py"
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


def build_zipapp(version: str) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / f"engram-{version}.pyz"
    if output.exists():
        output.unlink()
    zipapp.create_archive(
        source=str(SRC),
        target=str(output),
        main="engram.cli:main",
        interpreter="/usr/bin/env python3",
        compressed=True,
    )
    output.chmod(0o755)
    return output


def build_source_archive(version: str) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / f"engram-{version}.tar.gz"
    if output.exists():
        output.unlink()
    with tarfile.open(output, "w:gz") as archive:
        for relative in ("README.md", "pyproject.toml"):
            archive.add(ROOT / relative, arcname=f"engram-{version}/{relative}")
        for directory in ("src", "tests", "scripts", ".github"):
            path = ROOT / directory
            if path.exists():
                archive.add(path, arcname=f"engram-{version}/{directory}")
    return output


def write_checksums(paths: list[Path]) -> Path:
    output = DIST / "checksums.txt"
    lines = [f"{_sha256(path)}  {path.name}" for path in paths]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build engram release artifacts")
    parser.parse_args(argv)

    version = _version()
    pyz = build_zipapp(version)
    source = build_source_archive(version)
    checksums = write_checksums([pyz, source])

    print(f"Built {pyz}")
    print(f"Built {source}")
    print(f"Wrote {checksums}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
