#!/usr/bin/env bash
# VidyaSetu: set up and start the app on macOS / Linux.   bash run_mac_linux.sh
set -e
cd "$(dirname "$0")"
PY=python3; command -v $PY >/dev/null || PY=python
[ -d .venv ] || $PY -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -r requirements.txt
pip install -r requirements-optional.txt >/dev/null 2>&1 || echo "(Optional extras skipped - the app still works.)"
[ -f .env ] || cp .env.example .env
echo "Starting VidyaSetu on http://localhost:5000  (Ctrl+C to stop)"
python app.py
