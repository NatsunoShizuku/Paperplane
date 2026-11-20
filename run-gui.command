#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[Paperplane] First run: creating .venv and installing dependencies..."
    PY_BIN=""
    if command -v python3.12 >/dev/null 2>&1; then
        PY_BIN="python3.12"
    elif command -v python3 >/dev/null 2>&1; then
        PY_BIN="python3"
    else
        echo "[Paperplane] Error: Python 3.11+ not found. Please install Python 3 first."
        read -rp "Press Enter to close..."
        exit 1
    fi
    "$PY_BIN" -m venv .venv
    ./.venv/bin/python -m pip install --no-cache-dir -r requirements.txt
fi

echo "[Paperplane] Starting GUI at http://127.0.0.1:8765 ..."
./.venv/bin/python -m paperplane gui "$@"
