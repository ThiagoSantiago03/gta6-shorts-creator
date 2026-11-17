#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "Iniciando GTA 6 Shorts Studio em http://127.0.0.1:8765 ..."

if [ -f "$DIR/.venv/bin/python" ]; then
    PYTHON="$DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

"$PYTHON" -m uvicorn app.web_server:app --host 127.0.0.1 --port 8765
