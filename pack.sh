#!/bin/sh
# Pack the addon into dist/easybones_v<version>.zip, manifest at zip root (Blender requires it).
# --validate: also run `blender --command extension validate` on the zip (needs blender in PATH).
# Stdout is only the zip path (release workflow captures it); everything else goes to stderr.
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^version *= *"\(.*\)"/\1/p' blender_manifest.toml)
OUT="dist/easybones_v$VERSION.zip"
mkdir -p dist
rm -f "$OUT"
zip -q "$OUT" __init__.py blender_manifest.toml
if [ "$1" = "--validate" ]; then
  command -v blender >/dev/null || { echo "blender not found in PATH" >&2; exit 1; }
  if blender --command extension validate "$OUT" >&2; then
    echo "validate: OK" >&2
  else
    echo "validate: FAILED" >&2
    exit 1
  fi
fi
echo "$OUT"
