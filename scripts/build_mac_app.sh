#!/usr/bin/env bash
# Build GLDtk.app + .dmg (unsigned, Apple Silicon). See README "Build the Mac app".
#   1. PyInstaller one-folder build of server.py  -> packaging/dist/gldtk-server
#   2. Haxe release builds (renderer.hxml, main.hxml)
#   3. electron-builder --mac dmg --arm64          -> ldtk-src/app/redist/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HAXE_STD_PATH="${HAXE_STD_PATH:-/opt/homebrew/lib/haxe/std}"
HAXE="${HAXE:-/opt/homebrew/bin/haxe}"

echo "==> 1/3 backend bundle (PyInstaller)"
PY="$ROOT/.venv/bin/python"
"$PY" -m pip install --quiet pyinstaller
(cd "$ROOT/packaging" && rm -rf dist build \
  && "$PY" -m PyInstaller --noconfirm --distpath dist --workpath build gldtk-server.spec)
"$ROOT/packaging/dist/gldtk-server/gldtk-server" --check

echo "==> 2/3 Haxe release builds"
(cd "$ROOT/ldtk-src" && "$HAXE" renderer.hxml && "$HAXE" main.hxml)

echo "==> 3/3 electron-builder"
cd "$ROOT/ldtk-src/app"
[ -d node_modules ] || npm install
rm -rf redist
CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder build --mac dmg --arm64 --publish never

DMG="$(ls "$PWD"/redist/*.dmg | head -1)"
echo "Built: $DMG"
echo "App:   $PWD/redist/mac-arm64/GLDtk.app"
