# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ──────────────────────────────────────────────────
FROM base AS ai-query-agent

WORKDIR /app

# Copy project files (except .dockerignore patterns)
COPY . .

# Ensure data directories exist
RUN mkdir -p data logs

# Environment defaults
ENV MOCK_MODE=true
ENV MCP_HTTP_HOST=0.0.0.0
ENV MCP_HTTP_PORT=8080
ENV HEADROOM_PROXY_URL=http://headroom-proxy:8787

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

ENTRYPOINT ["python", "mcp_http_gateway.py"]