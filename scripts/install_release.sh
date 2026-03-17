#!/usr/bin/env sh
set -eu

OWNER=${ENGRAM_GITHUB_OWNER:-gaurav-yadav}
REPO=${ENGRAM_GITHUB_REPO:-Engram}
TARGET=${ENGRAM_INSTALL_PATH:-"$HOME/.local/bin/engram"}
REQUESTED_VERSION=${1:-latest}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd python3
require_cmd install

latest_version() {
  api_url="https://api.github.com/repos/$OWNER/$REPO/releases/latest"
  python3 - "$api_url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url) as response:
    payload = json.load(response)
tag = payload.get("tag_name")
if not isinstance(tag, str) or not tag:
    raise SystemExit("could not determine latest release tag")
print(tag)
PY
}

if [ "$REQUESTED_VERSION" = "latest" ]; then
  VERSION=$(latest_version)
else
  VERSION=$REQUESTED_VERSION
fi

VERSION_NUMBER=${VERSION#v}
ASSET="engram-$VERSION_NUMBER.pyz"
URL="https://github.com/$OWNER/$REPO/releases/download/$VERSION/$ASSET"

tmpdir=$(mktemp -d)
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT INT TERM

echo "Downloading $URL"
curl -fsSL "$URL" -o "$tmpdir/engram"

mkdir -p "$(dirname "$TARGET")"
install -m 755 "$tmpdir/engram" "$TARGET"

echo "Installed engram $VERSION to $TARGET"
echo "Run: $TARGET doctor"
