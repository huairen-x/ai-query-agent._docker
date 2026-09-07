"""
Node 8: 审计记录节点
记录全链路审计日志到 SQLite
"""
from __future__ import annotations
import json
import time
import uuid
from graph.state import AgentState
from db.manager import GLOBAL_DB_MANAGER
from observability import trace_node


@trace_node("audit")
def audit_node(state: AgentState) -> dict:
    """审计记录节点"""
    start = time.time()
    now = time.time()

    # 计算总耗时
    started_at = state.get("started_at", now)
    total_latency_ms = (now - started_at) * 1000

    # 构建审计日志条目
    steps = [
        ("cleanup", "上下文清理", "cleanup_result"),
        ("analyze", "需求分析", "keywords"),
        ("metadata", "元数据查询", "metadata"),
        ("sql_generation", "SQL 生成", "sql"),
        ("validation", "SQL 审查", "validation"),
        ("execution", "查询执行", "query_result"),
        ("interpretation", "结果解读", "interpretation"),
    ]

    audit_trail = []
    for step_name, step_label, state_key in steps:
        data = state.get(state_key)
        if data is not None:
            entry = {
                "step": step_name,
                "label": step_label,
                "data_preview": _truncate_json(data, 200),
                "timestamp": time.time(),
            }
            audit_trail.append(entry)

    # 写入 SQLite
    session_id = state.get("session_id", str(uuid.uuid4()))
    tenant_id = state.get("tenant_id", "default")
    question = state.get("question", "")
    sql = state.get("sql", "")

    try:
        GLOBAL_DB_MANAGER.insert("sessions", {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "question": question,
            "workflow_state": json.dumps(_get_workflow_summary(state), ensure_ascii=False),
            "status": "completed" if not state.get("errors") else "failed",
            "cache_hits": json.dumps(state.get("cache_hits", {}), ensure_ascii=False),
            "errors": json.dumps(state.get("errors", []), ensure_ascii=False),
            "started_at": started_at,
            "completed_at": now,
            "created_at": now,
        })
    except Exception:
        pass

    # 记录每个步骤的详细审计
    for step_name, step_label, state_key in steps:
        data = state.get(state_key)
        if data is not None:
            try:
                GLOBAL_DB_MANAGER.insert("audit_logs", {
                    "id": str(uuid.uuid4()),
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "question": question,
                    "workflow_step": step_name,
                    "node_name": step_label,
                    "input_data": json.dumps({"question": question}, ensure_ascii=False),
                    "output_data": _truncate_json(data, 500),
                    "token_estimate": _estimate_tokens(str(data)),
                    "compression_ratio": 1.0,
                    "latency_ms": 0,
                    "cache_hit": 0,
                    "error": "",
                    "created_at": time.time(),
                })
            except Exception:
                pass

    return {
        "audit_trail": audit_trail,
        "completed_at": now,
        "total_latency_ms": total_latency_ms,
    }


def _get_workflow_summary(state: AgentState) -> dict:
    """获取工作流摘要"""
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


def _truncate_json(data, max_len: int = 500) -> str:
    """截断 JSON 序列化"""
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
        if len(text) > max_len:
            text = text[:max_len] + "..."
        return text
    except Exception:
        return str(data)[:max_len]


def _estimate_tokens(text: str) -> int:
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en_chars = len(text) - cn_chars
    return int(cn_chars * 1.5 + en_chars * 0.25)