#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"
export HEADROOM_HOST="${HEADROOM_HOST:-127.0.0.1}"
export HEADROOM_PORT="${HEADROOM_PORT:-8787}"
export OPENAI_TARGET_API_URL="${OPENAI_TARGET_API_URL:-http://127.0.0.1:9999/v1}"
exec "$AI_QUERY_VENV_PROXY/bin/headroom" proxy
