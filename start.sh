#!/usr/bin/env bash
# Starts the GLDtk Python backend and LDtk desktop editor together.
# First-run setup (frontend pull, Python venv, Electron npm deps) is automatic.
# When LDtk closes, the backend is shut down automatically.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[GLDtk] LDtk closed, stopping backend (PID $SERVER_PID) …"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# 1. Fetch prebuilt LDtk frontend (includes precompiled main.js & renderer.js) if missing
if [ ! -f "$ROOT/ldtk-src/app/package.json" ]; then
  echo "[GLDtk] Fetching prebuilt LDtk frontend into ldtk-src/ …"
  git clone --depth 1 https://github.com/bekiryan/ldtk.git "$ROOT/ldtk-src"
fi

# 2. Bootstrap Python virtual environment on first run
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

# 3. Install Electron dependencies on first run
if [ ! -d "$ROOT/ldtk-src/app/node_modules" ]; then
  echo "[GLDtk] Installing Electron dependencies (ldtk-src/app) …"
  (cd "$ROOT/ldtk-src/app" && npm install)
fi

# 4. Start backend in background
echo "[GLDtk] Starting backend on http://127.0.0.1:8765 …"
python "$ROOT/server.py" &
SERVER_PID=$!

# Give the server a moment to bind the port
sleep 1

# 5. Start LDtk (foreground — blocks until LDtk window closes)
echo "[GLDtk] Starting LDtk …"
cd "$ROOT/ldtk-src/app"
npm start

cleanup
SERVER_PID=""
echo "[GLDtk] Done."
