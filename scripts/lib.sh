#!/usr/bin/env bash
# 共享路径与默认值。所有脚本通过 source 引入。

AI_QUERY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AI_QUERY_ROOT

AI_QUERY_VENV_AGENT="${AI_QUERY_VENV_AGENT:-${XDG_DATA_HOME:-$HOME/.local/share}/ai-query-agent/venv-agent}"
AI_QUERY_VENV_PROXY="${AI_QUERY_VENV_PROXY:-${XDG_DATA_HOME:-$HOME/.local/share}/ai-query-agent/venv-proxy}"
AI_QUERY_STATE="${AI_QUERY_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/ai-query-agent}"
AI_QUERY_CONF="${AI_QUERY_CONF:-${XDG_CONFIG_HOME:-$HOME/.config}/ai-query-agent}"
export AI_QUERY_VENV_AGENT AI_QUERY_VENV_PROXY AI_QUERY_STATE AI_QUERY_CONF

: "${AGENT_DATA_DIR:=$AI_QUERY_STATE/data}"
export AGENT_DATA_DIR

# venv 里的 C++ wheel（onnxruntime 等）运行期需要 libstdc++；
# nix-python 输出已并入 gcc.cc.lib，随 out-link 一起被 GC root 住。
if [ -d "$AI_QUERY_STATE/nix-python/lib" ]; then
  export LD_LIBRARY_PATH="$AI_QUERY_STATE/nix-python/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
