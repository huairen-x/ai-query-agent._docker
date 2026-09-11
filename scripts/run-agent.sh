#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"
cd "$AI_QUERY_ROOT"
export PYTHONUNBUFFERED=1
: "${AGENT_DATA_DIR:=$AI_QUERY_STATE/data}"
export AGENT_DATA_DIR
mkdir -p "$AGENT_DATA_DIR"

if [ "${MCP_SERVER:-gunicorn}" = "flask" ]; then
  exec "$AI_QUERY_VENV_AGENT/bin/python" mcp_http_gateway.py
fi

# --timeout 0: /sse 是长连接生成器，默认 30s worker 超时会杀掉 SSE
# --workers 1: SSE 会话表 mcp_sessions 是进程内字典，多 worker 会导致 /message 找不到会话
exec "$AI_QUERY_VENV_AGENT/bin/gunicorn" \
  --bind "${MCP_HTTP_HOST:-127.0.0.1}:${MCP_HTTP_PORT:-8080}" \
  --workers "${MCP_WORKERS:-1}" \
  --worker-class gthread \
  --threads "${MCP_THREADS:-16}" \
  --timeout 0 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  mcp_http_gateway:app
