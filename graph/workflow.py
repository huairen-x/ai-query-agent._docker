"""
LangGraph 工作流组装
将 8 个节点组装为状态机图
"""
from __future__ import annotations
import time
from typing import Literal
from langgraph.graph import StateGraph, START, END

import tracing
from graph.state import AgentState, create_initial_state
from compressor.engine import GLOBAL_HEADROOM
from graph.nodes import (
    cleanup_node,
    analyze_node,
    metadata_node,
    sql_generation_node,
    validation_node,
    should_retry_sql,
    execution_node,
    interpretation_node,
    audit_node,
)


def _instrument(name: str, node):
    """包裹节点：记录真实起止时间/耗时/trace_id，供审计落库与链路日志使用。

    audit 节点自身不计时——它已在统计各节点耗时。
    节点抛异常时保留已记录的耗时，并原样向上抛（由调用方决定重试语义）。
    """

    def wrapped(state: AgentState) -> dict:
        if name == "audit":
            return node(state)

        trace_id = state.get("trace_id") or tracing.current_or_new_trace_id()
        start = time.time()
        ratio_mark = GLOBAL_HEADROOM.mark()
        tracing.log_event("node.start", node=name, trace_id=trace_id, attempt=state.get("validation_attempts", 0))
        try:
            result = node(state)
        except Exception as exc:
            timings = dict(state.get("node_timings") or {})
            timings[name] = {
                "attempt": state.get("validation_attempts", 0) + 1,
                "started_at": start,
                "ended_at": time.time(),
                "latency_ms": round((time.time() - start) * 1000, 3),
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "compression_ratios": GLOBAL_HEADROOM.ratios_since(ratio_mark),
            }
            tracing.log_event(
                "node.error", level=tracing.logging.ERROR, node=name,
                trace_id=trace_id, duration_ms=timings[name]["latency_ms"],
                error=timings[name]["error"],
            )
            raise
        else:
            ended = time.time()
            timings = dict(state.get("node_timings") or {})
            timings[name] = {
                "attempt": state.get("validation_attempts", 0) + 1,
                "started_at": start,
                "ended_at": ended,
                "latency_ms": round((ended - start) * 1000, 3),
                "status": "ok",
                "error": "",
                "compression_ratios": GLOBAL_HEADROOM.ratios_since(ratio_mark),
            }
            tracing.log_event(
                "node.end", node=name, trace_id=trace_id, duration_ms=timings[name]["latency_ms"]
            )
            if isinstance(result, dict):
                result = {**result, "node_timings": timings}
            return result

    wrapped.__name__ = f"{name}_node"
    return wrapped


def create_workflow() -> StateGraph:
    """
    创建 LangGraph 状态机工作流

    流程:
    START → cleanup → analyze → metadata → sql_generation → validation
    → (retry ↻ sql_generation | pass → execution → interpretation → audit → END)
    """
    builder = StateGraph(AgentState)

    # 注册节点（经 _instrument 包裹以记录真实耗时与链路日志）
    builder.add_node("cleanup", _instrument("cleanup", cleanup_node))
    builder.add_node("analyze", _instrument("analyze", analyze_node))
    builder.add_node("metadata", _instrument("metadata", metadata_node))
    builder.add_node("sql_generation", _instrument("sql_generation", sql_generation_node))
    builder.add_node("validation", _instrument("validation", validation_node))
    builder.add_node("execution", _instrument("execution", execution_node))
    builder.add_node("interpretation", _instrument("interpretation", interpretation_node))
    builder.add_node("audit", _instrument("audit", audit_node))

    # 定义边
    builder.add_edge(START, "cleanup")
    builder.add_edge("cleanup", "analyze")
    builder.add_edge("analyze", "metadata")
    builder.add_edge("metadata", "sql_generation")
    builder.add_edge("sql_generation", "validation")

    # 条件分支：验证失败则重试
    builder.add_conditional_edges(
        "validation",
        should_retry_sql,
        {
            "retry": "sql_generation",
            "pass": "execution",
            "force_pass": "execution",
        },
    )

    builder.add_edge("execution", "interpretation")
    builder.add_edge("interpretation", "audit")
    builder.add_edge("audit", END)

    return builder.compile()


def run_workflow(question: str, tenant_id: str = "default", trace_id: str = "") -> dict:
    """
    运行工作流

    Args:
        question: 用户问题
        tenant_id: 租户 ID
        trace_id: 全链路追踪 ID（缺省沿用当前上下文，无则新建）

    Returns:
        工作流最终状态
    """
    workflow = create_workflow()
    initial_state = create_initial_state(question, tenant_id, trace_id or None)
    result = workflow.invoke(initial_state)
    return result


# 全局工作流实例
GLOBAL_WORKFLOW = create_workflow()