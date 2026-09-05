"""
LangGraph 工作流组装
将 8 个节点组装为状态机图
"""
from __future__ import annotations
from typing import Literal
from langgraph.graph import StateGraph, START, END

from graph.state import AgentState, create_initial_state
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


def create_workflow() -> StateGraph:
    """
    创建 LangGraph 状态机工作流

    流程:
    START → cleanup → analyze → metadata → sql_generation → validation
    → (retry ↻ sql_generation | pass → execution → interpretation → audit → END)
    """
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("cleanup", cleanup_node)
    builder.add_node("analyze", analyze_node)
    builder.add_node("metadata", metadata_node)
    builder.add_node("sql_generation", sql_generation_node)
    builder.add_node("validation", validation_node)
    builder.add_node("execution", execution_node)
    builder.add_node("interpretation", interpretation_node)
    builder.add_node("audit", audit_node)

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


def run_workflow(question: str, tenant_id: str = "default") -> dict:
    """
    运行工作流

    Args:
        question: 用户问题
        tenant_id: 租户 ID

    Returns:
        工作流最终状态
    """
    workflow = create_workflow()
    initial_state = create_initial_state(question, tenant_id)
    result = workflow.invoke(initial_state)
    return result


# 全局工作流实例
GLOBAL_WORKFLOW = create_workflow()