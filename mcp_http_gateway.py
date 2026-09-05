"""
MCP HTTP/SSE Gateway - 企业级智能问数系统
集成 LangGraph 状态机工作流 + SQLite 持久化缓存 + Headroom 压缩
"""
import json
import os
import sys
import time
import uuid
import threading
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

# 初始化数据库
from db.schema import init_db
init_db()

from graph.workflow import GLOBAL_WORKFLOW
from graph.state import create_initial_state
from cache.sqlite_cache import GLOBAL_SEMANTIC_CACHE, GLOBAL_METADATA_CACHE, GLOBAL_RESULT_CACHE
from compressor.cleanup import GLOBAL_CONTEXT_CLEANER
from compressor.engine import GLOBAL_HEADROOM
from db.manager import GLOBAL_DB_MANAGER

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MOCK_MODE = os.environ.get("MOCK_MODE", "true").lower() == "true"

print(f"[Gateway] 企业级智能问数系统启动")
print(f"[Gateway] Mock 模式: {MOCK_MODE}")
print(f"[Gateway] 数据库: {os.path.join(BASE_DIR, 'data', 'agent.db')}")

# ============================================================
# 工具定义
# ============================================================
MCP_TOOLS = {
    "ask_question": {
        "description": "完整智能问数流程：分析需求→查元数据→生成SQL→审查→执行→解读",
        "input": {
            "question": "string (必填) - 用户自然语言问题",
            "tenant_id": "string (可选) - 租户ID，默认 'default'",
        },
        "output": {
            "answer": "string - 自然语言回答",
            "sql": "string - 生成的 SQL",
            "data": "list - 查询结果数据",
            "chart_suggestion": "dict - 图表建议",
            "session_id": "string - 会话ID",
            "latency_ms": "float - 总耗时",
        },
    },
    "query_metadata": {
        "description": "查询表结构信息",
        "input": {"table_name": "string (必填) - 表名"},
        "output": {"columns": "list", "partition_keys": "list", "table_type": "string"},
    },
    "search_tables": {
        "description": "按关键词搜索相关表",
        "input": {"keyword": "string (必填) - 搜索关键词"},
        "output": {"tables": "list"},
    },
    "validate_sql": {
        "description": "SQL 安全/语法/性能审查",
        "input": {"sql": "string (必填) - 待审查的 SQL"},
        "output": {"safe": "bool", "valid": "bool", "warnings": "list", "errors": "list"},
    },
    "execute_sql": {
        "description": "执行 SQL 查询（Mock 模式）",
        "input": {"sql": "string (必填) - 要执行的 SQL"},
        "output": {"rows": "list", "row_count": "int", "columns": "list"},
    },
    "get_workflow_status": {
        "description": "查询工作流状态",
        "input": {"session_id": "string (必填) - 会话ID"},
        "output": {"status": "string", "question": "string", "steps": "list"},
    },
    "get_cache_stats": {
        "description": "获取缓存统计信息",
        "input": {},
        "output": {"semantic_cache": "dict", "metadata_cache": "dict", "result_cache": "dict"},
    },
    "get_headroom_stats": {
        "description": "获取 Headroom 压缩统计",
        "input": {},
        "output": {"compressions": "int", "strategies": "dict"},
    },
    "get_audit_logs": {
        "description": "查询审计日志",
        "input": {
            "session_id": "string (可选) - 按会话ID过滤",
            "limit": "int (可选) - 返回条数，默认 20",
        },
        "output": {"logs": "list", "total": "int"},
    },
}

# ============================================================
# 工具实现
# ============================================================

def _handle_ask_question(params: dict) -> dict:
    """处理完整问数流程"""
    question = params.get("question", "").strip()
    tenant_id = params.get("tenant_id", "default")

    if not question:
        return {"error": "question 不能为空"}

    try:
        # 运行 LangGraph 工作流
        start = time.time()
        result = GLOBAL_WORKFLOW.invoke(create_initial_state(question, tenant_id))
        elapsed = (time.time() - start) * 1000

        interpretation = result.get("interpretation", {})
        chart_suggestion = result.get("chart_suggestion", {})
        query_result = result.get("query_result", {})

        # 构建回答
        answer_parts = []
        summary = interpretation.get("summary", "")
        detail = interpretation.get("detail", "")
        if summary:
            answer_parts.append(summary)
        if detail:
            answer_parts.append(detail)

        errors = result.get("errors", [])
        if errors:
            answer_parts.append(f"（注意：处理过程中遇到 {len(errors)} 个问题）")

        return {
            "answer": "。".join(answer_parts) if answer_parts else "查询完成",
            "sql": result.get("sql", ""),
            "data": query_result.get("rows", []) if isinstance(query_result, dict) else [],
            "columns": query_result.get("columns", []) if isinstance(query_result, dict) else [],
            "chart_suggestion": chart_suggestion,
            "session_id": result.get("session_id", ""),
            "latency_ms": round(elapsed, 1),
            "row_count": result.get("row_count", 0),
            "validation_passed": result.get("validation_passed", False),
            "cache_hits": result.get("cache_hits", {}),
        }
    except Exception as e:
        return {"error": f"工作流执行失败: {str(e)}"}


def _handle_query_metadata(params: dict) -> dict:
    """查询表结构"""
    table_name = params.get("table_name", "")
    if not table_name:
        return {"error": "table_name 不能为空"}

    # 检查缓存
    cached = GLOBAL_METADATA_CACHE.get("describe_table", table_name=table_name)
    if cached:
        return {"cached": True, **cached}

    # Mock 数据
    metadata = {
        "table_name": table_name,
        "columns": [
            {"name": "id", "type": "bigint", "comment": "主键ID"},
            {"name": "dt", "type": "string", "comment": "分区日期"},
            {"name": "sale_amount", "type": "decimal(18,2)", "comment": "销售金额"},
            {"name": "sale_qty", "type": "int", "comment": "销售数量"},
            {"name": "store_code", "type": "string", "comment": "门店编码"},
            {"name": "store_name", "type": "string", "comment": "门店名称"},
            {"name": "region", "type": "string", "comment": "区域"},
            {"name": "visit_count", "type": "int", "comment": "到店数量"},
        ],
        "partition_keys": ["dt"],
        "table_type": "MANAGED_TABLE",
    }
    GLOBAL_METADATA_CACHE.set(metadata, "describe_table", table_name=table_name)
    return metadata


def _handle_search_tables(params: dict) -> dict:
    """搜索相关表"""
    keyword = params.get("keyword", "")
    if not keyword:
        return {"tables": []}

    all_tables = [
        {"table_name": "dwd_sale_order_di", "comment": "销售订单明细"},
        {"table_name": "dwd_sale_order_item_di", "comment": "销售订单行项目"},
        {"table_name": "dim_product_df", "comment": "产品维度表"},
        {"table_name": "dim_store_df", "comment": "门店维度表"},
        {"table_name": "dwd_traffic_visit_di", "comment": "到店流量明细"},
        {"table_name": "dwd_customer_visit_di", "comment": "客户到访明细"},
    ]

    kw = keyword.lower()
    matched = []
    for t in all_tables:
        if kw in t["table_name"].lower() or kw in t["comment"].lower():
            matched.append(t)
    return {"tables": matched if matched else all_tables[:3]}


def _handle_validate_sql(params: dict) -> dict:
    """SQL 审查"""
    sql = params.get("sql", "").strip()
    if not sql:
        return {"safe": False, "valid": False, "warnings": ["SQL 为空"], "errors": ["SQL 为空"]}

    from graph.nodes.validation import _check_safety, _check_syntax, _assess_performance

    safety = _check_safety(sql)
    syntax = _check_syntax(sql)
    performance = _assess_performance(sql)

    return {
        "safe": safety["safe"],
        "valid": syntax["valid"],
        "warnings": safety["warnings"] + performance["warnings"],
        "errors": [] if syntax["valid"] else [syntax["error"]],
    }


def _handle_execute_sql(params: dict) -> dict:
    """执行 SQL"""
    sql = params.get("sql", "").strip()
    if not sql:
        return {"rows": [], "row_count": 0, "columns": []}

    # 检查结果缓存
    cached = GLOBAL_RESULT_CACHE.get(sql)
    if cached:
        return {"cached": True, **cached}

    # Mock 执行
    from graph.nodes.execution import _mock_execute, _get_columns
    rows = _mock_execute(sql, "general")
    columns = _get_columns("general")

    GLOBAL_RESULT_CACHE.set(sql, {"rows": rows, "columns": columns}, row_count=len(rows))
    return {"rows": rows, "row_count": len(rows), "columns": columns, "cached": False}


def _handle_get_workflow_status(params: dict) -> dict:
    """查询工作流状态"""
    session_id = params.get("session_id", "")
    if not session_id:
        return {"error": "session_id 不能为空"}

    row = GLOBAL_DB_MANAGER.fetch_one(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    )
    if not row:
        return {"error": f"未找到会话: {session_id}"}

    return {
        "session_id": row["session_id"],
        "status": row["status"],
        "question": row["question"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


def _handle_get_cache_stats(params: dict) -> dict:
    return {
        "semantic_cache": GLOBAL_SEMANTIC_CACHE.get_stats().__dict__,
        "metadata_cache": GLOBAL_METADATA_CACHE.get_stats().__dict__,
        "result_cache": GLOBAL_RESULT_CACHE.get_stats().__dict__,
        "context_cleaner": GLOBAL_CONTEXT_CLEANER.get_stats(),
        "headroom": GLOBAL_HEADROOM.get_stats() if hasattr(GLOBAL_HEADROOM, 'get_stats') else {},
        "db": GLOBAL_DB_MANAGER.get_stats(),
    }


def _handle_get_headroom_stats(params: dict) -> dict:
    stats = {"compressions": 0, "strategies": {}}
    if hasattr(GLOBAL_HEADROOM, 'get_stats'):
        stats = GLOBAL_HEADROOM.get_stats()
    return stats


def _handle_get_audit_logs(params: dict) -> dict:
    session_id = params.get("session_id", "")
    limit = params.get("limit", 20)

    if session_id:
        logs = GLOBAL_DB_MANAGER.fetch_all(
            "SELECT * FROM audit_logs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit)
        )
    else:
        logs = GLOBAL_DB_MANAGER.fetch_all(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    return {"logs": logs, "total": len(logs)}


# ============================================================
# 工具路由表
# ============================================================
TOOL_HANDLERS = {
    "ask_question": _handle_ask_question,
    "query_metadata": _handle_query_metadata,
    "search_tables": _handle_search_tables,
    "validate_sql": _handle_validate_sql,
    "execute_sql": _handle_execute_sql,
    "get_workflow_status": _handle_get_workflow_status,
    "get_cache_stats": _handle_get_cache_stats,
    "get_headroom_stats": _handle_get_headroom_stats,
    "get_audit_logs": _handle_get_audit_logs,
}

# ============================================================
# HTTP 路由
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "mock_mode": MOCK_MODE,
        "version": "2.0.0",
        "tools": list(MCP_TOOLS.keys()),
        "cache_db": os.path.join(BASE_DIR, "data", "agent.db"),
    })


@app.route("/tools", methods=["GET"])
def list_tools():
    """列出所有 MCP 工具"""
    return jsonify({"tools": MCP_TOOLS})


@app.route("/tools/<tool_name>", methods=["POST"])
def call_tool(tool_name):
    """调用 MCP 工具"""
    if tool_name not in TOOL_HANDLERS:
        return jsonify({"error": f"未知工具: {tool_name}"}), 404

    params = request.get_json(force=True, silent=True) or {}
    handler = TOOL_HANDLERS[tool_name]

    try:
        result = handler(params)
        if "error" in result:
            return jsonify(result), 400
        return jsonify({"result": result, "tool": tool_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mcp", methods=["POST"])
def mcp_endpoint():
    """
    MCP 统一端点
    支持 JSON-RPC 风格调用
    """
    body = request.get_json(force=True, silent=True) or {}
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id", str(uuid.uuid4()))

    if method.startswith("tools/"):
        tool_name = method.split("/")[1]
        if tool_name in TOOL_HANDLERS:
            try:
                result = TOOL_HANDLERS[tool_name](params)
                if "error" in result:
                    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": result["error"]}})
                return jsonify({"jsonrpc": "2.0", "id": req_id, "result": result})
            except Exception as e:
                return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(e)}})
        return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"未知工具: {tool_name}"}})

    if method == "list_tools":
        return jsonify({"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}})

    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"未知方法: {method}"}})


# ============================================================
# Trae 兼容 SSE 端点
# ============================================================
sse_sessions = {}

@app.route("/sse/ask", methods=["GET"])
def sse_ask():
    """SSE 流式问数"""
    sid = str(uuid.uuid4())
    sse_sessions[sid] = {"created_at": time.time(), "done": False}

    def event_stream():
        yield f"event: endpoint\ndata: /sse/ask/message?session_id={sid}\n\n"
        while not sse_sessions.get(sid, {}).get("done", True):
            yield f": heartbeat\n\n"
            time.sleep(15)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/sse/ask/message", methods=["POST"])
def sse_ask_message():
    """SSE 消息端点"""
    body = request.get_json(force=True, silent=True) or {}
    question = body.get("question", "")
    if not question:
        return jsonify({"error": "question 不能为空"}), 400

    result = _handle_ask_question({"question": question})
    return jsonify(result)


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("MCP_HTTP_PORT", "8080"))
    host = os.environ.get("MCP_HTTP_HOST", "0.0.0.0")
    print(f"[Gateway] 启动 HTTP 服务: http://{host}:{port}")
    print(f"[Gateway] 端点列表:")
    print(f"  Health:     GET  http://{host}:{port}/health")
    print(f"  Tools:      GET  http://{host}:{port}/tools")
    print(f"  Tool Call:  POST http://{host}:{port}/tools/<tool_name>")
    print(f"  MCP:        POST http://{host}:{port}/mcp")
    print(f"  SSE:        GET  http://{host}:{port}/sse/ask")
    app.run(host=host, port=port, threaded=True)