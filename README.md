# AI Query Agent — Docker Deployment

企业级 AI 智能问数系统（NL2SQL Agent）的 Docker 容器化部署方案。

基于 LangGraph 状态机工作流 + Headroom 本地压缩（SmartCrusher Rust 后端，0 Token 消耗）+ SQLite 持久化缓存，通过 MCP 协议与 Trae IDE 集成。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                       Docker Compose                             │
│                                                                   │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────┐│
│  │  mock-upstream    │───>│  headroom-proxy  │<───│ ai-query-    ││
│  │  (port 9999)      │    │  (port 8787)      │    │ agent        ││
│  │  模拟 LLM API     │    │  SmartCrusher     │    │ (port 8080)  ││
│  │                   │    │  本地压缩 0 Token  │    │ LangGraph    ││
│  │                   │    │  Dashboard 可视    │    │ NL2SQL 引擎  ││
│  └──────────────────┘    └──────────────────┘    └──────────────┘
                                    │
                                    ▼
                          ┌──────────────────┐
                          │  Headroom         │
                          │  Dashboard        │
                          │  :8787/dashboard  │
                          │  压缩统计监控      │
                          └──────────────────┘
```

## 服务组件

| 服务 | 端口 | 说明 |
|------|------|------|
| `mock-upstream` | 9999 | 模拟 LLM API 上游，用于测试压缩效果 |
| `headroom-proxy` | 8787 | Headroom 压缩代理 + Dashboard，SmartCrusher 本地无损压缩 |
| `ai-query-agent` | 8080 | 主服务：LangGraph 8 节点工作流 + SQLite 缓存 + MCP 网关 |

## 快速开始

### 前置条件

- Docker Engine 24.0+
- Docker Compose v2.0+

### 启动服务

```bash
# 克隆仓库
git clone https://github.com/huairen-x/ai-query-agent._docker.git
cd ai-query-agent._docker

# 构建并启动所有服务
docker-compose up -d --build

# 查看启动日志
docker-compose logs -f

# 确认所有服务健康
docker-compose ps
```

### 验证部署

```bash
# 1. 检查 Mock Upstream
curl http://localhost:9999/health
# → {"status":"ok","service":"mock-upstream"}

# 2. 检查 Headroom Proxy
curl http://localhost:8787/health

# 3. 检查 AI Query Agent
curl http://localhost:8080/health

# 4. 查看 Headroom Dashboard
# 浏览器打开 http://localhost:8787/dashboard
```

## 系统工作流

系统基于 LangGraph 状态机，包含 8 个有序节点：

```
用户问题
  │
  ▼
[1] cleanup        ── 上下文预清理（去重/去空/过滤/截断）
  │
  ▼
[2] analyze        ── 意图分析 + 关键词提取
  │
  ▼
[3] metadata       ── 元数据查询 + SQLite 缓存 + 压缩
  │
  ▼
[4] sql_generation ── SQL 生成
  │
  ▼
[5] validation     ── SQL 安全校验（语法/注入/性能）
  │
  ▼
[6] execution      ── 查询执行 + 结果缓存 + 压缩
  │
  ▼
[7] interpretation ── 结果解读 + 图表建议
  │
  ▼
[8] audit          ── 全链路审计日志
  │
  ▼
返回结果给用户
```

## 压缩策略

所有环节均集成了 Headroom SmartCrusher（Rust 后端）本地压缩，**0 Token 消耗**：

| 内容类型 | 压缩策略 | 说明 |
|----------|----------|------|
| 对话历史 | 15% 目标比 | 历史消息去重聚类 |
| 元数据 | 25% 目标比 | 表结构、字段信息压缩 |
| SQL 代码 | 30% 目标比 | 注释移除 + 空格压缩 |
| 查询结果 | 10% 目标比 | 结构化数据去重压缩 |
| System Prompt | 35% 目标比 | 提示词裁剪 |
| 业务术语表 | 25% 目标比 | 术语表精简 |

## 配置说明

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MOCK_MODE` | `true` | Mock 模式（无需真实数据源） |
| `MCP_HTTP_HOST` | `0.0.0.0` | 服务绑定地址 |
| `MCP_HTTP_PORT` | `8080` | 服务端口 |
| `HEADROOM_PROXY_URL` | `http://headroom-proxy:8787` | Headroom 代理地址 |
| `HEADROOM_ENABLED` | `true` | 是否启用压缩 |
| `HEADROOM_TARGET_RATIO` | `0.3` | 默认压缩目标比 |

### 数据持久化

```yaml
volumes:
  - ./data:/app/data    # SQLite 数据库文件
  - ./logs:/app/logs    # 应用日志
```

## MCP 配置（Trae IDE）

在 Trae IDE 中配置 MCP Server 连接本系统：

```json
{
  "mcpServers": {
    "ai-query-agent": {
      "url": "http://localhost:8080/mcp",
      "type": "http"
    }
  }
}
```

然后通过自然语言提问即可触发问数工作流，例如：
- "查询最近一年到店量趋势"
- "销售额排名前 10 的产品"
- "各区域销售对比分析"

## 运维命令

```bash
# 查看实时日志
docker-compose logs -f ai-query-agent

# 查看压缩统计
docker-compose logs -f headroom-proxy

# 重启单个服务
docker-compose restart ai-query-agent

# 停止所有服务
docker-compose down

# 停止并清除数据
docker-compose down -v

# 缩容副本
docker-compose up -d --scale ai-query-agent=3
```

## Headroom Dashboard

访问 http://localhost:8787/dashboard 可查看：

- 实时压缩统计（压缩次数、节省 Token 数）
- 每请求压缩率
- 历史趋势
- 策略分布

## 项目结构

```
ai-query-agent._docker/
├── Dockerfile                    # AI Query Agent 镜像
├── Dockerfile.headroom           # Headroom 代理镜像
├── docker-compose.yml            # 三服务编排
├── docker-headroom-entrypoint.sh # Headroom 容器启动脚本
├── .dockerignore                 # 构建排除规则
├── requirements.txt              # Python 依赖
├── .env.example                  # 环境变量模板
│
├── mcp_http_gateway.py           # MCP HTTP 网关入口
├── mock_upstream_server.py       # Mock LLM 上游
├── audit_logger.py               # 审计日志
│
├── compressor/                   # 压缩引擎（Headroom 封装）
│   ├── __init__.py
│   ├── engine.py                 # SmartCrusher 本地压缩引擎
│   ├── cleanup.py                # 上下文预清理器
│   └── strategies/               # 按内容类型的压缩策略
│       ├── conversation.py
│       ├── metadata.py
│       ├── sql.py
│       ├── result.py
│       ├── prompt.py
│       └── glossary.py
│
├── graph/                        # LangGraph 工作流
│   ├── workflow.py               # 8 节点状态机定义
│   ├── state.py                  # AgentState 定义
│   └── nodes/                    # 各节点实现
│       ├── cleanup.py
│       ├── analyze.py
│       ├── metadata.py
│       ├── sql_generation.py
│       ├── validation.py
│       ├── execution.py
│       ├── interpretation.py
│       └── audit.py
│
├── cache/                        # SQLite 缓存层
│   └── sqlite_cache.py           # 语义/元数据/结果三级缓存
│
├── db/                           # 数据库层
│   ├── schema.py                 # SQLite 表结构
│   └── manager.py                # 数据库管理器
│
├── engine/                       # 核心引擎配置
│   └── config.py                 # 全局配置管理
│
└── templates/                    # 响应模板
```

## 性能优化特性

- **SmartCrusher 本地压缩**：Rust 后端实现，0 Token 消耗，平均压缩率 45%+
- **上下文预清理**：在压缩前移除无效上下文（去重/去空/过滤噪音）
- **三级 SQLite 缓存**：语义缓存（相似问题命中）、元数据缓存、结果缓存
- **全链路审计**：每一步操作记录到 SQLite 审计表，可追溯可复现
- **LangGraph 状态机**：8 节点可编排工作流，支持重试、超时、条件分支

## 许可证

MIT