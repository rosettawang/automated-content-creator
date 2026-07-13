#!/usr/bin/env bash
# API safety-net test suite (see editor/specs/../specs/test-suite.md).
# Runs against the Flask test client — the real app does NOT need to be running.
set -euo pipefail
cd "$(dirname "$0")/editor"

PY=./venv/bin/python
[ -x "$PY" ] || PY=python3

exec "$PY" -m pytest tests -q "$@"
