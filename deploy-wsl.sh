#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# WSL + nix + Docker 一键部署脚本
# 用法: bash deploy-wsl.sh
# 前置条件: WSL 已安装，网络连通
# ============================================================

echo "=== 1. 安装 nix 包管理器 ==="
if ! command -v nix &>/dev/null; then
    sh <(curl -L https://nixos.org/nix/install) --no-daemon
    . "$HOME/.nix-profile/etc/profile.d/nix.sh"
fi

echo "=== 2. 通过 nix 安装 Docker ==="
if ! command -v docker &>/dev/null; then
    nix-env -iA nixpkgs.docker
    nix-env -iA nixpkgs.docker-compose
fi

echo "=== 3. 启动 Docker 守护进程 ==="
sudo dockerd &>/dev/null &
sleep 3
sudo docker info >/dev/null 2>&1 || { echo "Docker 启动失败，请检查"; exit 1; }

echo "=== 4. 构建镜像并启动 ==="
PROJECT_DIR="/workspace/AgentProject"

# 如果 D 盘挂载在 /mnt/d
if [ -d "/mnt/d/AIproject/AgentProject" ]; then
    PROJECT_DIR="/mnt/d/AIproject/AgentProject"
fi

cd "$PROJECT_DIR"

echo "构建 headroom 镜像..."
docker compose build headroom-proxy

echo "构建 AI Query Agent 镜像..."
docker compose build ai-query-agent

echo "启动所有服务..."
docker compose up -d

echo "=== 5. 检查服务状态 ==="
sleep 5
docker compose ps

echo ""
echo "=== 部署完成 ==="
echo " Mock Upstream:  http://localhost:9999/health"
echo " Headroom Proxy: http://localhost:8787/dashboard"
echo " AI Query Agent: http://localhost:8080/health"
echo ""
echo "查看日志: docker compose logs -f"
echo "停止服务: docker compose down"