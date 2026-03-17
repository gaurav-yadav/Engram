#!/usr/bin/env sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 /path/to/codemem-<version>.pyz [target]" >&2
  exit 2
fi

artifact=$1
target=${2:-"$HOME/.local/bin/codemem"}

if [ ! -f "$artifact" ]; then
  echo "artifact not found: $artifact" >&2
  exit 1
fi

mkdir -p "$(dirname "$target")"
install -m 755 "$artifact" "$target"

echo "Installed codemem to $target"
echo "Run: $target doctor"
