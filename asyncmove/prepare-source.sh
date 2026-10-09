#!/bin/sh
# Export the async-move-4.1.3 branch at HEAD, with every submodule (recursively),
# into ./transmission-src.tar for the Docker build context. Refuses a dirty tree.
set -eu
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
OUT=$(cd "$(dirname "$0")" && pwd)/transmission-src.tar
cd "$REPO"
if [ -n "$(git status --porcelain --ignore-submodules=none)" ]; then
  echo "working tree is dirty; commit first" >&2; exit 1
fi
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
git archive --format=tar HEAD | tar -x -C "$TMP"
git submodule foreach --quiet --recursive \
  "git archive --format=tar --prefix=\"\$displaypath/\" HEAD | tar -x -C '$TMP'"
git rev-parse HEAD > "$TMP/.asyncmove-source-commit"
tar -C "$TMP" -cf "$OUT" .
echo "wrote $OUT from $(git rev-parse --short=10 HEAD)"
