#!/usr/bin/env bash
# Launches GLDtk as a single combined desktop app on macOS (zero dependencies).
# Pass `--dev` (or run on Linux) to run the Python backend + LDtk Electron from source.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_ZIP_URL="https://github.com/bekiryan/GLDtk/releases/download/v1.5.3/GLDtk-macos-arm64.app.zip"
DEV_MODE=0
if [ "${1:-}" = "--dev" ]; then
  DEV_MODE=1
fi

# ---------------------------------------------------------------------------
# macOS single-app mode (default): no Python, Node.js, npm, or Haxe needed
# ---------------------------------------------------------------------------
if [ "$DEV_MODE" -eq 0 ] && [ "$(uname -s)" = "Darwin" ]; then
  APP_PATH=""
  for candidate in \
    "$ROOT/GLDtk.app" \
    "$ROOT/ldtk-src/app/redist/mac-arm64/GLDtk.app" \
    "/Applications/GLDtk.app"; do
    if [ -d "$candidate" ]; then
      APP_PATH="$candidate"
      break
    fi
  done

  if [ -z "$APP_PATH" ]; then
    echo "[GLDtk] Downloading combined GLDtk.app (v1.5.3) …"
    TMP_ZIP="$(mktemp -t gldtk-app.XXXXXX).zip"
    trap 'rm -f "$TMP_ZIP"' EXIT
    curl -fL --progress-bar "$APP_ZIP_URL" -o "$TMP_ZIP"
    echo "[GLDtk] Extracting GLDtk.app …"
    ditto -x -k "$TMP_ZIP" "$ROOT"
    rm -f "$TMP_ZIP"
    trap - EXIT
    APP_PATH="$ROOT/GLDtk.app"
  fi

  xattr -cr "$APP_PATH" 2>/dev/null || true
  echo "[GLDtk] Opening $APP_PATH …"
  open -a "$APP_PATH"
  exit 0
fi

# ---------------------------------------------------------------------------
# Developer source mode (`./start.sh --dev` or Linux)
# ---------------------------------------------------------------------------
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[GLDtk] LDtk closed, stopping backend (PID $SERVER_PID) …"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [ ! -f "$ROOT/ldtk-src/app/package.json" ]; then
  echo "[GLDtk] Fetching prebuilt LDtk frontend into ldtk-src/ …"
  git clone --depth 1 https://github.com/bekiryan/ldtk.git "$ROOT/ldtk-src"
fi

if [ ! -d "$ROOT/.venv" ]; then
  echo "[GLDtk] Creating Python environment (.venv) and installing backend …"
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/python" -m pip install --upgrade pip
  "$ROOT/.venv/bin/pip" install -e "$ROOT[server]"
fi

if [ -f "$ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

if [ ! -d "$ROOT/ldtk-src/app/node_modules" ]; then
  echo "[GLDtk] Installing Electron dependencies (ldtk-src/app) …"
  (cd "$ROOT/ldtk-src/app" && npm install)
fi

echo "[GLDtk] Starting backend on http://127.0.0.1:8765 …"
python "$ROOT/server.py" &
SERVER_PID=$!

sleep 1

echo "[GLDtk] Starting LDtk …"
cd "$ROOT/ldtk-src/app"
npm start

cleanup
SERVER_PID=""
echo "[GLDtk] Done."
