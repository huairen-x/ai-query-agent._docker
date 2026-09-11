#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"
cd "$AI_QUERY_ROOT"
exec "$AI_QUERY_VENV_PROXY/bin/python" mock_upstream_server.py
