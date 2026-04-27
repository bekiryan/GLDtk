#!/usr/bin/env bash
# Starts the GLDtk Python backend and LDtk together.
# When LDtk closes, the backend is shut down automatically.

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# Activate venv if present
if [ -f "$ROOT/.venv/bin/activate" ]; then
  source "$ROOT/.venv/bin/activate"
fi

# Start backend in background
echo "[GLDtk] Starting backend on http://127.0.0.1:8765 …"
python "$ROOT/server.py" &
SERVER_PID=$!

# Give the server a moment to bind the port
sleep 1

# Start LDtk (foreground — script blocks here until LDtk window closes)
echo "[GLDtk] Starting LDtk …"
cd "$ROOT/ldtk-src/app"
npm start

# LDtk exited — kill the backend
echo "[GLDtk] LDtk closed, stopping backend (PID $SERVER_PID) …"
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
echo "[GLDtk] Done."
