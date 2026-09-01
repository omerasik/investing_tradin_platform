#!/usr/bin/env bash
# Shell wrapper for scripts/dev.py
set -euo pipefail

if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

exec "$PYTHON" scripts/dev.py "$@"
