#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_URL="${HEADROOM_UPSTREAM_URL:-http://localhost:9999/v1}"
PROXY_PORT="${HEADROOM_PROXY_PORT:-8787}"
DASHBOARD_PORT="${HEADROOM_DASHBOARD_PORT:-8788}"

# Start mock upstream in background if MOCK_MODE=true
if [ "${MOCK_MODE:-true}" = "true" ]; then
    echo "[Headroom] Starting Mock Upstream on port 9999..."
    python mock_upstream_server.py &
    MOCK_PID=$!
    sleep 2
fi

# Start headroom proxy
echo "[Headroom] Starting Headroom Proxy on port ${PROXY_PORT}..."
echo "[Headroom] Upstream: ${UPSTREAM_URL}"
headroom proxy --port "${PROXY_PORT}" --openai-api-url "${UPSTREAM_URL}" &
PROXY_PID=$!
sleep 3

# Start headroom dashboard on separate port
echo "[Headroom] Starting Headroom Dashboard on port ${DASHBOARD_PORT}..."
echo "[Headroom] Dashboard: http://localhost:${DASHBOARD_PORT}/dashboard"
headroom dashboard --port "${DASHBOARD_PORT}" --no-open &
DASHBOARD_PID=$!

echo "[Headroom] All services started."
echo "  Mock Upstream:  http://localhost:9999/health"
echo "  Headroom Proxy: http://localhost:${PROXY_PORT}/v1/chat/completions"
echo "  Dashboard:      http://localhost:${DASHBOARD_PORT}/dashboard"

trap "echo '[Headroom] Stopping...'; kill ${MOCK_PID:-} ${PROXY_PID:-} ${DASHBOARD_PID:-} 2>/dev/null; exit 0" SIGTERM SIGINT

wait