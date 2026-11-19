#!/usr/bin/env bash
# Build GLDtk.app + .dmg (unsigned, Apple Silicon). See README "Build the Mac app".
#   1. PyInstaller one-folder build of server.py  -> packaging/dist/gldtk-server
#   2. Haxe release builds (renderer.hxml, main.hxml)
#   3. electron-builder --mac dmg --arm64          -> ldtk-src/app/redist/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${HAXE_STD_PATH:-}" ] && [ -d /opt/homebrew/lib/haxe/std ]; then
  export HAXE_STD_PATH=/opt/homebrew/lib/haxe/std
fi
HAXE="${HAXE:-$(command -v haxe || echo /opt/homebrew/bin/haxe)}"

echo "==> 1/3 backend bundle (PyInstaller)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
"$PY" -m pip install --quiet pyinstaller
(cd "$ROOT/packaging" && rm -rf dist build \
  && "$PY" -m PyInstaller --noconfirm --distpath dist --workpath build gldtk-server.spec)
"$ROOT/packaging/dist/gldtk-server/gldtk-server" --check

echo "==> 2/3 Haxe release builds"
(cd "$ROOT/ldtk-src" && "$HAXE" renderer.hxml && "$HAXE" main.hxml)

echo "==> 3/3 electron-builder (ad-hoc signed .app + .dmg)"
cd "$ROOT/ldtk-src/app"
[ -d node_modules ] || npm install
rm -rf redist
CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder build --mac dir --arm64 --publish never

APP="$PWD/redist/mac-arm64/GLDtk.app"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

cat > "$PWD/redist/mac-arm64/Open GLDtk (First Run).command" <<'EOF'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
APP=""
for candidate in "$DIR/GLDtk.app" "/Applications/GLDtk.app" "$HOME/Downloads/GLDtk.app"; do
  if [ -d "$candidate" ]; then APP="$candidate"; break; fi
done
if [ -z "$APP" ]; then
  echo "GLDtk.app not found next to this script or in /Applications."
  exit 1
fi
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
codesign --verify --deep --strict "$APP" >/dev/null 2>&1 || codesign --force --deep --sign - "$APP"
open -a "$APP"
EOF
chmod +x "$PWD/redist/mac-arm64/Open GLDtk (First Run).command"

CSC_IDENTITY_AUTO_DISCOVERY=false npx electron-builder build --prepackaged "$APP" --mac dmg --arm64 --publish never

DMG="$(ls "$PWD"/redist/*.dmg | head -1)"
echo "Built: $DMG"
echo "App:   $APP"
