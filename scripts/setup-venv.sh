#!/usr/bin/env bash
# 依赖准备：两个 venv 分别精确复现两份 lock（幂等；--force 重建）
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/lib.sh"

PYTHON="${AI_QUERY_PYTHON:-$AI_QUERY_STATE/nix-python/bin/python}"
: "${PIP_INDEX_URL:=https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_INDEX_URL

[ -x "$PYTHON" ] || {
  echo "缺少解释器 $PYTHON；先执行 nix build --out-link \"$AI_QUERY_STATE/nix-python\" .#python" >&2
  exit 1
}

setup_one() {
  local venv="$1" lock="$2" extra="$3"
  if [ -d "$venv" ]; then
    echo "[setup] 已存在：$venv（--force 可重建）"
  else
    "$PYTHON" -m venv "$venv"
  fi
  "$venv/bin/pip" install --upgrade pip >/dev/null
  "$venv/bin/pip" install -r "$AI_QUERY_ROOT/$lock"
  [ -z "$extra" ] || "$venv/bin/pip" install -r "$AI_QUERY_ROOT/$extra"
}

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
if [ "$FORCE" = 1 ]; then rm -rf "$AI_QUERY_VENV_AGENT" "$AI_QUERY_VENV_PROXY"; fi

mkdir -p "$AI_QUERY_STATE"
setup_one "$AI_QUERY_VENV_AGENT" requirements-agent.lock.txt requirements-dev.txt
setup_one "$AI_QUERY_VENV_PROXY" requirements-proxy.lock.txt ""

echo "[setup] 完成"
"$AI_QUERY_VENV_AGENT/bin/python" -c "import flask, langgraph, headroom; print('[setup] agent venv OK')"
"$AI_QUERY_VENV_PROXY/bin/python" -c "import headroom, fastapi, uvicorn; print('[setup] proxy venv OK')"
