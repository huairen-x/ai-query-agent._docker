"""
MCP HTTP/SSE Gateway - 企业级智能问数系统
标准 MCP 协议 (Streamable HTTP + SSE) + 兼容性 HTTP 端点
"""
import json
import os
import sys
import time
import uuid
import queue
import threading
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

import tracing
from tracing import TRACE_HEADER, CLIENT_SESSION_HEADER, preview, log_event

tracing.setup_logging()
logger = tracing.logging.getLogger("gateway")

# 初始化数据库
from db.schema import init_db, get_db_path
init_db()

import maintenance
maintenance.start()

from graph.workflow import GLOBAL_WORKFLOW
from graph.state import create_initial_state
from cache.sqlite_cache import GLOBAL_SEMANTIC_CACHE, GLOBAL_METADATA_CACHE, GLOBAL_RESULT_CACHE
from compressor.cleanup import GLOBAL_CONTEXT_CLEANER
from compressor.engine import GLOBAL_HEADROOM
from db.manager import GLOBAL_DB_MANAGER
from datasource.mock_warehouse import GLOBAL_WAREHOUSE, WarehouseError

app = Flask(__name__)
CORS(app)

app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MCP_MAX_BODY_BYTES", str(256 * 1024)))

SERVER_VERSION = "2.1.0"

# ============================================================
# 全链路追踪：每个请求分配 trace_id，随响应头返回，并写入审计
# ============================================================

@app.before_request
def _begin_trace():
    # 客户端可自带 trace id（便于与上游系统对齐），否则新生成
    tracing.set_trace_id(request.headers.get(TRACE_HEADER) or None)
    # 会话标识：MCP 走 ?session_id=（一次 SSE 流内的多次工具调用共享），
    # REST 走 X-Client-Session 头；两者都没有则留空
    tracing.set_client_session(
        request.args.get("session_id") or request.headers.get(CLIENT_SESSION_HEADER)
    )
    request.environ["trace_start"] = time.time()


@app.after_request
def _end_trace(response):
    trace_id = tracing.get_trace_id()
    response.headers[TRACE_HEADER] = trace_id
    client_session = tracing.get_client_session()
    if client_session:
        response.headers[CLIENT_SESSION_HEADER] = client_session
    start = request.environ.get("trace_start")
    if start is not None:
        log_event(
            "http.request",
            method=request.method,
            path=request.path,
            status=response.status_code,
            duration_ms=round((time.time() - start) * 1000, 3),
            client=request.headers.get("User-Agent", "")[:80],
            trace_id=trace_id,
            client_session=client_session,
        )
    if response.status_code >= 400:
        log_event(
            "http.error",
            level=tracing.logging.WARNING,
            path=request.path,
            status=response.status_code,
        )
    return response


@app.errorhandler(413)
def _payload_too_large(_error):
    limit = app.config["MAX_CONTENT_LENGTH"]
    _audit_guard(
        guard="body_limit",
        detail={"limit_bytes": limit, "content_length": request.content_length},
        tool="(transport)",
        error=f"请求体超过上限 {limit} 字节",
    )
    return jsonify({"error": f"请求体超过上限 {limit} 字节", "trace_id": tracing.get_trace_id()}), 413


# ============================================================
# 审计：所有工具调用（含直连 REST / MCP / 旧版 /mcp）统一落 audit_logs
# ============================================================

def _blocking_error(result) -> str:
    """导致 HTTP 400 / isError 的硬错误（保持原有的 execute_sql 语义）"""
    if not isinstance(result, dict):
        return ""
    return str(result.get("error") or result.get("sql_error") or "")


def _audit_error(result, blocking: str) -> str:
    """审计 error 列：硬错误之外，把安全/语法审查不通过也记下来（不改变响应码）"""
    if blocking:
        return blocking
    if isinstance(result, dict) and (result.get("safe") is False or result.get("valid") is False):
        # 审查结论要完整：errors 说明硬失败，warnings 说明命中了哪条规则
        issues = list(result.get("errors") or []) + list(result.get("warnings") or [])
        return "; ".join(str(i) for i in dict.fromkeys(issues)) or "审查未通过"
    return ""


def _audit_write(*, tool: str, params: dict, result, error: str, latency_ms: float,
                 tenant_id: str = "default", session_id: str = "") -> None:
    """写一条工具调用审计（网关层，覆盖全部工具，含失败与拦截）"""
    try:
        GLOBAL_DB_MANAGER.insert("audit_logs", {
            "id": str(uuid.uuid4()),
            # session_id = 工作流会话；trace_id = 本次调用；client_session = 客户端会话
            "session_id": session_id,
            "tenant_id": tenant_id,
            "question": json.dumps(params, ensure_ascii=False, default=str)[:500],
            "workflow_step": "tool_call",
            "node_name": tool,
            # SQL 全量存 sql_text（input_data 是截断预览，长语句会看不全）
            "sql_text": str(params.get("sql", "")) if isinstance(params, dict) else "",
            "input_data": preview(params, 500),
            "output_data": preview(result, 500),
            "token_estimate": 0,
            "compression_ratio": 1.0,
            "latency_ms": round(latency_ms, 3),
            "cache_hit": 1 if isinstance(result, dict) and result.get("cached") else 0,
            "error": error,
            "created_at": time.time(),
            "trace_id": tracing.get_trace_id(),
            "client_session": tracing.get_client_session(),
            "client": request.headers.get("User-Agent", "")[:120],
            "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        })
    except Exception as exc:  # 审计失败不影响主流程，但必须留痕（历史上审计曾静默全失效）
        logger.error("审计写入失败 tool=%s: %s: %s", tool, type(exc).__name__, exc,
                     exc_info=True)


def _audit_guard(*, guard: str, detail: dict, tool: str, error: str) -> None:
    """记录一次守卫拦截（传输层/工具层/执行层）"""
    log_event("guard.blocked", level=tracing.logging.WARNING, guard=guard, tool=tool, error=error)
    _audit_write(
        tool=f"guard:{guard}",
        params=detail,
        result={"blocked": True, "guard": guard},
        error=error,
        latency_ms=0.0,
    )


def dispatch_tool(tool_name: str, params: dict) -> tuple[dict, int]:
    """统一工具分发：审计 + 守卫 + 计时。返回 (payload, http_status)"""
    start = time.time()
    trace_id = tracing.get_trace_id()

    if tool_name not in TOOL_HANDLERS:
        error = f"未知工具: {tool_name}"
        _audit_write(tool=tool_name or "(empty)", params=params, result=None, error=error,
                     latency_ms=(time.time() - start) * 1000)
        return {"error": error}, 404

    tenant_id = str(params.get("tenant_id", "default")) if isinstance(params, dict) else "default"

    try:
        result = TOOL_HANDLERS[tool_name](params)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("工具执行异常 tool=%s trace=%s", tool_name, trace_id)
        _audit_write(tool=tool_name, params=params, result=None, error=error,
                     latency_ms=(time.time() - start) * 1000, tenant_id=tenant_id)
        return {"error": error, "trace_id": trace_id}, 500

    latency_ms = (time.time() - start) * 1000
    blocking = _blocking_error(result)
    audit_error = _audit_error(result, blocking)
    session_id = result.get("session_id", "") if isinstance(result, dict) else ""

    if not blocking and audit_error:
        # 审查未通过的 SQL（如 validate_sql 返回 safe=false）：记录但不改响应码
        log_event("guard.flagged", level=tracing.logging.WARNING, guard="sql_review",
                  tool=tool_name, error=audit_error)

    _audit_write(tool=tool_name, params=params, result=result, error=audit_error,
                 latency_ms=latency_ms, tenant_id=tenant_id, session_id=session_id)

    if blocking:
        # 执行/校验失败：400，并带上 trace_id 便于排查
        payload = result if isinstance(result, dict) else {"error": blocking}
        payload.setdefault("trace_id", trace_id)
        return payload, 400
    return result, 200

print(f"[Gateway] MCP 服务启动")
print(f"[Gateway] 数据源: mock-warehouse (sqlite/hive-compatible)")
print(f"[Gateway] 数据库: {get_db_path()}")

# ============================================================
# 服务端用法指引
# 同时出现在 initialize 响应的 instructions 与各工具描述中，
# 让客户端 LLM 一接入就知道该走哪条链路。
# ============================================================
USAGE_GUIDE = """本服务是「SQL 执行 + 元数据」后端，不提供自然语言转 SQL 的 LLM 能力。

推荐调用顺序（由你生成 SQL，本服务负责执行）：
  1. search_tables        不带关键词返回全部表；带关键词（支持中文）按表名/注释检索
  2. query_metadata       取目标表的字段、类型、注释、分区键、行数
  3. 你根据表结构自行编写 Hive SQL
  4. validate_sql         （可选）执行前自查安全/语法/性能
  5. execute_sql          执行 SQL，拿到 rows / columns / row_count

要点：
- 数据仓库是 Hive 风格，支持 JOIN、子查询、CTE、窗口函数与常用 Hive 函数
  （date_sub / date_add / current_date() / nvl / concat_ws / substring / date_format / year / month / day）。
- 时间范围请用 date_sub(current_date(), N) / date_add(current_date(), N)，
  不要写 date '...' 或 'yyyy-MM-dd' - N 这类字面量运算（会被拒绝）。
- execute_sql 仅允许单条 SELECT/WITH；写操作、多语句会被拒绝并返回错误原因。
- SQL 报错会原样返回（含 sql_error / errors 字段），请据错误信息改写后重试，
  不要因为一次失败就放弃；表名、列名务必以上一步 query_metadata 的结果为准。
- ask_question 是内置的规则式演示链路（不调用 LLM，覆盖范围有限），
  正式问数请用上面的 execute_sql 链路。"""

MCP_TOOLS = [
    {
        "name": "search_tables",
        "description": "【问数第 1 步】探查可用表。不带 keyword 返回全部表；带 keyword（支持中文，如\"到店\"、\"销售\"、\"sale\"）按表名或表注释检索。返回每张表的 table_name 与 comment。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词；留空或不传则返回全部表"},
            },
            "required": [],
        },
    },
    {
        "name": "query_metadata",
        "description": "【问数第 2 步】查询指定表的完整结构：列名、类型、注释、分区键、行数。写 SQL 前必须先调本工具确认字段名，不要凭猜测拼列名。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "表名，如 dwd_sale_order_di；不确定时先调 search_tables"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "validate_sql",
        "description": "【问数第 3 步，可选】执行前审查 SQL，返回 safe / valid / warnings / errors。仅做静态检查，不执行、不返回数据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "待审查的 SQL"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "execute_sql",
        "description": "【问数第 4 步 · 主力工具】执行你编写的只读 Hive SQL，返回 rows / columns / row_count。支持 JOIN、子查询、CTE、窗口函数与常用 Hive 函数（date_sub/date_add/current_date()/nvl/concat_ws/substring/date_format 等）。仅允许单条 SELECT/WITH，写操作与多语句会被拒绝。日期范围用 date_sub(current_date(), N)，勿用日期字面量加减。执行失败会在 sql_error 中原样返回数据库错误，请据此改写 SQL 后重试。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "要执行的单条只读 SQL（Hive 语法）"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "ask_question",
        "description": "【演示链路，非主力】用服务端内置规则把自然语言转成 SQL 并执行，不调用 LLM。仅能识别「时间窗口 + 区域/门店/产品维度」这类简单问题，复杂问数请改走 search_tables → query_metadata → execute_sql。返回 answer/sql/data/columns/chart_suggestion，可作为表结构与可用 SQL 的示例参考。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "自然语言业务问题，如\"查询最近30天到店量趋势\""},
                "tenant_id": {"type": "string", "description": "租户ID，默认 default"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_workflow_status",
        "description": "查询工作流状态",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话ID"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "get_cache_stats",
        "description": "获取缓存统计信息",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_headroom_stats",
        "description": "获取 Headroom 压缩统计",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_audit_logs",
        "description": "查询审计日志。client_session 返回一段客户端会话（如一次对话）的全部调用，按时间正序；trace_id 返回单次调用的完整链路（网关 + 各工作流节点）；session_id 返回一次问数工作流的全部步骤。limit 钳制在 1~500。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "client_session": {"type": "string", "description": "按客户端会话过滤（MCP 的 SSE session_id / REST 的 X-Client-Session）"},
                "session_id": {"type": "string", "description": "按工作流会话ID过滤"},
                "trace_id": {"type": "string", "description": "按全链路追踪ID过滤（响应头 X-Trace-Id）"},
                "limit": {"type": "number", "description": "返回条数，默认 20，最大 500"},
            },
        },
    },
]

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
        start = time.time()
        result = GLOBAL_WORKFLOW.invoke(
            create_initial_state(question, tenant_id, tracing.get_trace_id())
        )
        elapsed = (time.time() - start) * 1000

        interpretation = result.get("interpretation", {})
        chart_suggestion = result.get("chart_suggestion", {})
        query_result = result.get("query_result", {})

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

        query_result = query_result if isinstance(query_result, dict) else {}
        payload = {
            "answer": "。".join(answer_parts) if answer_parts else "查询完成",
            "sql": result.get("sql", ""),
            "data": query_result.get("rows", []),
            "columns": query_result.get("columns", []),
            "chart_suggestion": chart_suggestion,
            "session_id": result.get("session_id", ""),
            "trace_id": result.get("trace_id", "") or tracing.get_trace_id(),
            "latency_ms": round(elapsed, 1),
            "row_count": result.get("row_count", 0),
            "validation_passed": result.get("validation_passed", False),
            "result_cache_hit": result.get("result_cache_hit", False),
            "cache_hits": result.get("cache_hits", {}),
        }
        # SQL 执行失败时把原因回传给调用方 LLM，便于其改写 SQL 后重试
        if errors:
            payload["errors"] = errors
        if query_result.get("error"):
            payload["sql_error"] = query_result["error"]
        if query_result.get("truncated"):
            payload["truncated"] = True
        return payload
    except Exception as e:
        return {"error": f"工作流执行失败: {str(e)}"}


def _headroom_observe(content_type: str, content):
    """旁路工具(execute_sql/query_metadata/search_tables)压缩并上报到 proxy。

    仅统计压缩/上报节省，不替换返回值，保证调用方拿到的仍是原始结构数据。
    """
    try:
        if not getattr(GLOBAL_HEADROOM, "enabled", False):
            return
        GLOBAL_HEADROOM.compress(content_type, content, context={})
    except Exception as e:
        print(f"[headroom-observe] FAIL {content_type}: {type(e).__name__}: {e}", flush=True)


def _handle_query_metadata(params: dict) -> dict:
    table_name = params.get("table_name", "").strip()
    if not table_name:
        return {"error": "table_name 不能为空"}

    cached = GLOBAL_METADATA_CACHE.get("describe_table", table_name=table_name)
    if cached:
        _headroom_observe("metadata", cached)
        return {"cached": True, **cached}

    try:
        metadata = GLOBAL_WAREHOUSE.describe(table_name)
    except WarehouseError as exc:
        return {"error": str(exc)}

    GLOBAL_METADATA_CACHE.set(metadata, "describe_table", table_name=table_name)
    _headroom_observe("metadata", metadata)
    return metadata


def _handle_search_tables(params: dict) -> dict:
    keyword = params.get("keyword", "").strip()

    # 不带关键词时返回全部表：Trae 探查阶段的第一步
    matched = GLOBAL_WAREHOUSE.search(keyword)
    result = {
        "keyword": keyword,
        "tables": matched,
        "total": len(matched),
    }
    _headroom_observe("metadata", result)
    return result


def _handle_validate_sql(params: dict) -> dict:
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
    sql = params.get("sql", "").strip()
    if not sql:
        return {"rows": [], "row_count": 0, "columns": []}

    cached = GLOBAL_RESULT_CACHE.get(sql)
    if cached:
        _headroom_observe("sql_result", {"rows": cached["rows"], "columns": cached["columns"]})
        return {
            "cached": True,
            "rows": cached["rows"],
            "row_count": cached["row_count"],
            "columns": cached["columns"],
        }

    try:
        executed = GLOBAL_WAREHOUSE.execute(sql)
    except WarehouseError as exc:
        return {"error": str(exc)}

    rows, columns = executed["rows"], executed["columns"]
    GLOBAL_RESULT_CACHE.set(sql, {"rows": rows, "columns": columns}, row_count=len(rows))
    _headroom_observe("sql_result", {"rows": rows, "columns": columns})

    result = {
        "cached": False,
        "rows": rows,
        "row_count": len(rows),
        "columns": columns,
    }
    if executed["truncated"]:
        result["truncated"] = True
    return result


def _handle_get_workflow_status(params: dict) -> dict:
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


def _clamp_limit(value, default: int = 20, maximum: int = 500) -> int:
    """把 limit 参数钳制到 [1, maximum]；非法值回退 default"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _handle_get_audit_logs(params: dict) -> dict:
    session_id = params.get("session_id", "")
    trace_id = params.get("trace_id", "")
    client_session = params.get("client_session", "")
    limit = _clamp_limit(params.get("limit", 20))

    if client_session:
        # 一段客户端会话（如 Trae 一次提问）的全部调用，按时间正序还原先后
        logs = GLOBAL_DB_MANAGER.fetch_all(
            "SELECT * FROM audit_logs WHERE client_session = ? ORDER BY created_at ASC LIMIT ?",
            (client_session, limit)
        )
        total = GLOBAL_DB_MANAGER.count("audit_logs", "client_session = ?", (client_session,))
    elif session_id:
        logs = GLOBAL_DB_MANAGER.fetch_all(
            "SELECT * FROM audit_logs WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit)
        )
        total = GLOBAL_DB_MANAGER.count("audit_logs", "session_id = ?", (session_id,))
    elif trace_id:
        logs = GLOBAL_DB_MANAGER.fetch_all(
            "SELECT * FROM audit_logs WHERE trace_id = ? ORDER BY created_at ASC LIMIT ?",
            (trace_id, limit)
        )
        total = GLOBAL_DB_MANAGER.count("audit_logs", "trace_id = ?", (trace_id,))
    else:
        logs = GLOBAL_DB_MANAGER.fetch_all(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        total = GLOBAL_DB_MANAGER.count("audit_logs")
    return {
        "logs": logs,
        "total": total,
        "trace_id": trace_id or tracing.get_trace_id(),
        "client_session": client_session or tracing.get_client_session(),
    }


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
# MCP 协议处理
# ============================================================

MCP_PROTOCOL_VERSION = "2024-11-05"


def _mcp_handle_initialize(params: dict) -> dict:
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "ai-query-agent", "version": SERVER_VERSION},
        # MCP 标准字段：客户端接入时把用法指引直接喂给其 LLM
        "instructions": USAGE_GUIDE,
    }


def _mcp_handle_list_tools(params: dict) -> dict:
    return {"tools": MCP_TOOLS}


def _mcp_handle_call_tool(params: dict) -> dict:
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    payload, status = dispatch_tool(name, arguments)
    if status != 200:
        return {
            "isError": True,
            "content": [{"type": "text", "text": payload.get("error", "工具调用失败")}],
        }
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
    }


MCP_METHODS = {
    "initialize": _mcp_handle_initialize,
    "tools/list": _mcp_handle_list_tools,
    "tools/call": _mcp_handle_call_tool,
    "notifications/initialized": lambda p: None,
    "notifications/cancelled": lambda p: None,
}

# ============================================================
# MCP SSE 传输
# ============================================================

mcp_sessions: dict[str, dict] = {}
mcp_sessions_lock = threading.Lock()


def _mcp_process_request(body: dict) -> dict:
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    tool_name = params.get("name", "-") if isinstance(params, dict) else "-"
    if os.environ.get("HEADROOM_DEBUG", "true").lower() == "true":
        if method == "tools/call":
            args = params.get("arguments", {})
            tool_args = json.dumps(args, ensure_ascii=False)[:200] if args else ""
            print(f"[MCP] method={method} tool={tool_name} args={tool_args}", flush=True)
        else:
            print(f"[MCP] method={method}", flush=True)

    if method in MCP_METHODS:
        result = MCP_METHODS[method](params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"未知方法: {method}"},
    }


@app.route("/sse", methods=["GET"])
def mcp_sse():
    """MCP 标准 SSE 端点"""
    session_id = str(uuid.uuid4())
    msg_queue = queue.Queue()

    with mcp_sessions_lock:
        mcp_sessions[session_id] = {"queue": msg_queue, "created_at": time.time()}

    # 该 session_id 会作为 client_session 写进本次会话所有调用的审计，便于整组反查
    log_event("sse.session.open", session_id=session_id,
              client=request.headers.get("User-Agent", "")[:80])

    def event_stream():
        try:
            yield f"event: endpoint\ndata: /message?session_id={session_id}\n\n"

            while True:
                try:
                    data = msg_queue.get(timeout=30)
                    if data is None:
                        break
                    yield f"event: message\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with mcp_sessions_lock:
                mcp_sessions.pop(session_id, None)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/message", methods=["POST"])
def mcp_message():
    """MCP 消息端点"""
    body = request.get_json(force=True, silent=True) or {}
    session_id = request.args.get("session_id", "")

    response = _mcp_process_request(body)

    # 如果有 SSE 会话，也推送到 SSE 流
    if session_id:
        with mcp_sessions_lock:
            session = mcp_sessions.get(session_id)
            if session:
                session["queue"].put(response)

    return jsonify(response)


# ============================================================
# 兼容性 HTTP 端点
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "version": SERVER_VERSION,
        "data_source": {
            "type": "mock-warehouse",
            "engine": "sqlite(hive-compatible)",
            "tables": list(GLOBAL_WAREHOUSE.table_names()),
        },
        "tools": [t["name"] for t in MCP_TOOLS],
        "cache_db": get_db_path(),
        "mcp": {
            "sse": "GET  /sse",
            "message": "POST /message",
            "protocol": MCP_PROTOCOL_VERSION,
        },
    })


@app.route("/tools", methods=["GET"])
def list_tools():
    return jsonify({"tools": {t["name"]: {"description": t["description"], "input": t["inputSchema"]} for t in MCP_TOOLS}})


@app.route("/tools/<tool_name>", methods=["POST"])
def call_tool(tool_name):
    params = request.get_json(force=True, silent=True) or {}
    payload, status = dispatch_tool(tool_name, params)
    if status == 200:
        return jsonify({"result": payload, "tool": tool_name, "trace_id": tracing.get_trace_id()})
    return jsonify(payload), status


@app.route("/mcp", methods=["POST"])
def mcp_endpoint():
    """MCP 统一端点（兼容旧版 JSON-RPC）"""
    body = request.get_json(force=True, silent=True) or {}
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id", str(uuid.uuid4()))

    if method.startswith("tools/"):
        tool_name = method.split("/", 1)[1]
        payload, status = dispatch_tool(tool_name, params)
        if status != 200:
            return jsonify({"jsonrpc": "2.0", "id": req_id,
                            "error": {"code": -32000, "message": payload.get("error", "")}})
        return jsonify({"jsonrpc": "2.0", "id": req_id, "result": payload})

    if method == "list_tools":
        return jsonify({"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}})

    return jsonify({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"未知方法: {method}"}})


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("MCP_HTTP_PORT", "8080"))
    host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
    print(f"[Gateway] 启动 HTTP 服务: http://{host}:{port}")
    print(f"[Gateway] MCP 端点:")
    print(f"  SSE:        GET  http://{host}:{port}/sse")
    print(f"  Message:    POST http://{host}:{port}/message?session_id=<id>")
    print(f"  Health:     GET  http://{host}:{port}/health")
    print(f"  Tools:      GET  http://{host}:{port}/tools")
    print(f"  Tool Call:  POST http://{host}:{port}/tools/<tool_name>")
    print(f"  MCP(旧):    POST http://{host}:{port}/mcp")
    print(f"[Gateway] 在 Trae 中配置 MCP 服务:")
    print(f"  URL: http://localhost:{port}/sse")
    app.run(host=host, port=port, threaded=True)