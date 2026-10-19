#!/bin/sh
# Pack the addon into dist/easybones_v<version>.zip, manifest at zip root (Blender requires it).
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^version *= *"\(.*\)"/\1/p' blender_manifest.toml)
OUT="dist/easybones_v$VERSION.zip"
mkdir -p dist
rm -f "$OUT"
zip -q "$OUT" __init__.py blender_manifest.toml
echo "$OUT"
