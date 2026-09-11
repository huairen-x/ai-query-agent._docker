#!/usr/bin/env bash
# 去 Docker 部署：Nix 提供解释器 → venv 装依赖 → systemd 用户单元托管
#
#   ./deploy-nix.sh            准备 + 启动 + 健康检查 + 冒烟（默认）
#   ./deploy-nix.sh --status   查看单元状态与 /health
#   ./deploy-nix.sh --stop     停止并禁用三个单元
#   ./deploy-nix.sh --test     跑单元测试
#   ./deploy-nix.sh --logs     跟踪网关日志
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
source scripts/lib.sh

UNITS=(mock-upstream headroom-proxy ai-query-agent)
ENV_FILE="$AI_QUERY_CONF/agent.env"
AGENT_BASE="${MCP_HTTP_HOST:-127.0.0.1}:${MCP_HTTP_PORT:-8080}"

find_nix() {
  command -v nix 2>/dev/null || {
    [ -x /nix/var/nix/profiles/default/bin/nix ] &&
      echo /nix/var/nix/profiles/default/bin/nix
  }
}

check_ports() {
  local busy=0
  for port in "${MCP_HTTP_PORT:-8080}" "${HEADROOM_PORT:-8787}" "${MOCK_UPSTREAM_PORT:-9999}"; do
    if ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then
      echo "[deploy] 警告：端口 $port 已被占用（可能是旧进程/其他服务）" >&2
      ss -ltnp "sport = :$port" 2>/dev/null | sed -n '2,3p' >&2
      busy=1
    fi
  done
  if [ "$busy" = 1 ]; then
    echo "[deploy] 如与本服务冲突，请改 MCP_HTTP_PORT / HEADROOM_PORT（并同步 HEADROOM_PROXY_URL）/ MOCK_UPSTREAM_PORT" >&2
  fi
}

generate_env() {
  mkdir -p "$AI_QUERY_CONF"
  if [ -f "$ENV_FILE" ]; then
    echo "[deploy] 复用已有配置 $ENV_FILE"
  else
    sed "s|^AGENT_DATA_DIR=.*|AGENT_DATA_DIR=$AI_QUERY_STATE/data|" .env.example > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "[deploy] 生成配置 $ENV_FILE（AGENT_DATA_DIR=$AI_QUERY_STATE/data）"
  fi
}

install_units() {
  local unit_dir
  unit_dir="$(dirname "$AI_QUERY_CONF")/systemd/user"
  mkdir -p "$unit_dir"
  install -m 644 systemd/*.service "$unit_dir/"
  systemctl --user daemon-reload
  systemctl --user enable --now "${UNITS[@]}"
}

wait_healthy() {
  for _ in $(seq 1 60); do
    if curl -sf "http://$AGENT_BASE/health" >/dev/null 2>&1; then
      echo "[deploy] /health 就绪: http://$AGENT_BASE/health"
      return 0
    fi
    sleep 1
  done
  echo "[deploy] 60s 内未就绪；查看 journalctl --user -u ai-query-agent -n 50" >&2
  return 1
}

smoke() {
  echo "[deploy] 冒烟：ask_question"
  curl -sf -X POST "http://$AGENT_BASE/tools/ask_question" \
    -H 'Content-Type: application/json' \
    -d '{"question":"查询最近30天到店量趋势"}' |
    python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print("  row_count =", r["row_count"])'

  echo "[deploy] 冒烟：execute_sql"
  curl -sf -X POST "http://$AGENT_BASE/tools/execute_sql" \
    -H 'Content-Type: application/json' \
    -d '{"sql":"SELECT p.category, SUM(s.sale_amount) AS amt FROM dwd_sale_order_di s JOIN dim_product_df p ON s.product_code=p.product_code WHERE s.dt >= date_sub(current_date(), 90) GROUP BY p.category ORDER BY amt DESC"}' |
    python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print("  row_count =", r["row_count"], "| first =", r["rows"][0])'
}

cmd_setup() {
  local nix
  nix="$(find_nix)" || true
  [ -n "${nix:-}" ] || { echo "[deploy] 找不到 nix；请先安装 Determinate Nix" >&2; exit 1; }

  mkdir -p "$AI_QUERY_STATE"
  check_ports
  generate_env

  echo "[deploy] nix build .#python → $AI_QUERY_STATE/nix-python"
  "$nix" build --out-link "$AI_QUERY_STATE/nix-python" .#python

  AI_QUERY_PYTHON="$AI_QUERY_STATE/nix-python/bin/python" bash scripts/setup-venv.sh

  mkdir -p "$AI_QUERY_STATE/data"
  install_units
  wait_healthy
  smoke

  cat <<EOF

[deploy] 完成。单元：${UNITS[*]}
[deploy] 数据目录：$AI_QUERY_STATE/data
[deploy] 若要 WSL 会话结束后保持服务运行，请执行一次：
           loginctl enable-linger $USER   （若提示需要认证则加 sudo）
EOF
}

case "${1:---setup}" in
  --setup) cmd_setup ;;
  --status)
    systemctl --user status "${UNITS[@]}" --no-pager || true
    echo "--- /health"
    curl -s "http://$AGENT_BASE/health" || echo "(不可达)"
    echo
    ;;
  --stop)
    systemctl --user disable --now "${UNITS[@]}"
    echo "[deploy] 已停止并禁用：${UNITS[*]}"
    ;;
  --test)
    bash scripts/run-tests.sh
    ;;
  --logs)
    exec journalctl --user -u ai-query-agent -f
    ;;
  *)
    echo "用法: $0 [--setup|--status|--stop|--test|--logs]" >&2
    exit 2
    ;;
esac
