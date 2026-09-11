"""
Node 8: 审计记录节点

落库内容：
    - sessions：整个问数的终态（含 trace_id、真实起止时间、耗时、缓存命中、错误）
    - audit_logs：每个工作流步骤一行，created_at / latency_ms 取该节点**真实**执行时间
      （由 graph/workflow.py 的 _instrument 包裹器记录在 state["node_timings"]），
      并带上 trace_id / tenant_id / cache_hit / compression_ratio / error

trace_id 与 HTTP 层共用（tracing.py 的 contextvar），因此网关日志、节点日志、
审计记录三者可用同一个 trace_id 串起来。
"""
from __future__ import annotations
import json
import time
import uuid

import tracing
from graph.state import AgentState
from db.manager import GLOBAL_DB_MANAGER
from compressor.engine import GLOBAL_HEADROOM

logger = tracing.logging.getLogger("audit")

# 步骤 → (state 取值键, 中文标签)；顺序即审计轨迹顺序
STEPS = [
    ("cleanup", "上下文清理", "cleanup_result"),
    ("analyze", "需求分析", "keywords"),
    ("metadata", "元数据查询", "metadata"),
    ("sql_generation", "SQL 生成", "sql"),
    ("validation", "SQL 审查", "validation"),
    ("execution", "查询执行", "query_result"),
    ("interpretation", "结果解读", "interpretation"),
]

# 各步骤是否视为缓存命中的取值键
_CACHE_HIT_KEYS = {
    "metadata": "metadata_cache_hit",
    "execution": "result_cache_hit",
}


def audit_node(state: AgentState) -> dict:
    """审计记录节点"""
    now = time.time()
    started_at = state.get("started_at", now)
    total_latency_ms = (now - started_at) * 1000

    session_id = state.get("session_id") or str(uuid.uuid4())
    tenant_id = state.get("tenant_id", "default")
    trace_id = state.get("trace_id") or tracing.current_or_new_trace_id()
    question = state.get("question", "")
    timings = state.get("node_timings") or {}

    # ── 构建审计轨迹（真实时间戳） ────────────────────────
    audit_trail = []
    for step_name, step_label, state_key in STEPS:
        data = state.get(state_key)
        if data is None:
            continue
        timing = timings.get(step_name, {})
        audit_trail.append({
            "step": step_name,
            "label": step_label,
            "data_preview": tracing.preview(data, 200),
            "timestamp": timing.get("ended_at", now),
            "latency_ms": timing.get("latency_ms", 0.0),
        })

    # ── sessions ─────────────────────────────────────────
    errors = state.get("errors", []) or []
    try:
        GLOBAL_DB_MANAGER.insert("sessions", {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "question": question,
            "workflow_state": json.dumps(_get_workflow_summary(state), ensure_ascii=False),
            "status": "completed" if not errors else "failed",
            "cache_hits": json.dumps(state.get("cache_hits", {}), ensure_ascii=False),
            "errors": json.dumps(errors, ensure_ascii=False),
            "started_at": started_at,
            "completed_at": now,
            "created_at": now,
            "trace_id": trace_id,
            "client_session": tracing.get_client_session(),
        })
    except Exception as exc:
        logger.warning("写入 sessions 失败 session=%s: %s", session_id, exc)

    # ── audit_logs：每个步骤一行，含真实耗时与压缩比 ────────
    sql_text = state.get("sql", "") or ""
    client_session = tracing.get_client_session()
    for step_name, step_label, state_key in STEPS:
        data = state.get(state_key)
        if data is None:
            continue
        timing = timings.get(step_name, {})
        output = tracing.preview(data, 500)
        try:
            GLOBAL_DB_MANAGER.insert("audit_logs", {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "tenant_id": tenant_id,
                "question": question,
                "workflow_step": step_name,
                "node_name": step_label,
                "sql_text": sql_text,
                "input_data": json.dumps({"question": question}, ensure_ascii=False),
                "output_data": output,
                "token_estimate": tracing.estimate_tokens(output),
                "compression_ratio": _step_compression_ratio(step_name, timing),
                "latency_ms": timing.get("latency_ms", 0.0),
                "cache_hit": 1 if state.get(_CACHE_HIT_KEYS.get(step_name, ""), False) else 0,
                "error": timing.get("error", "") or _node_error(step_name, state),
                "created_at": timing.get("ended_at", now),
                "trace_id": trace_id,
                "client_session": client_session,
                "client": "workflow",
                "remote_addr": "",
            })
        except Exception as exc:
            logger.warning(
                "写入 audit_logs 失败 step=%s session=%s: %s", step_name, session_id, exc
            )

    tracing.log_event(
        "workflow.audit",
        session_id=session_id,
        trace_id=trace_id,
        steps=len(audit_trail),
        total_latency_ms=round(total_latency_ms, 3),
        errors=len(errors),
    )

    return {
        "audit_trail": GLOBAL_HEADROOM.compress("audit_trail", audit_trail).data
        if GLOBAL_HEADROOM.enabled
        else audit_trail,
        "completed_at": now,
        "total_latency_ms": total_latency_ms,
    }


def _step_compression_ratio(step_name: str, timing: dict) -> float:
    """该步骤期间发生的压缩比（压缩后/压缩前）；未压缩记为 1.0"""
    ratios = timing.get("compression_ratios") or []
    return ratios[0] if ratios else 1.0


def _node_error(step_name: str, state: AgentState) -> str:
    """从 validation / query_result 中提取该步骤的错误信息"""
    if step_name == "validation":
        validation = state.get("validation") or {}
        errors = validation.get("errors") or validation.get("warnings") or []
        return "; ".join(str(e) for e in errors)
    if step_name == "execution":
        query_result = state.get("query_result") or {}
        if isinstance(query_result, dict):
            return str(query_result.get("error", ""))
    return ""


def _get_workflow_summary(state: AgentState) -> dict:
    """工作流摘要"""
    return {
        "question": state.get("question", ""),
        "intent": state.get("intent", ""),
        "complexity": state.get("complexity", ""),
        "sql": state.get("sql", ""),
        "validation_passed": state.get("validation_passed", False),
        "row_count": state.get("row_count", 0),
        "errors": state.get("errors", []),
        "cache_hits": state.get("cache_hits", {}),
    }
