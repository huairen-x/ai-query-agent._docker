"""
LangGraph 工作流组装
将 8 个节点组装为状态机图
"""
from __future__ import annotations
import time
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
from engine.config import GLOBAL_CONFIG
from resilience import timeout_scope, TimeoutError
from observability import get_logger
from compressor.pipeline import GLOBAL_CONTEXT_PIPELINE


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


def run_workflow_with_timeout(question: str, tenant_id: str = "default") -> dict:
    """
    带超时的工作流执行

    超时时间由 GLOBAL_CONFIG.resilience.workflow_timeout 控制
    超时时返回部分状态 + 错误信息
    """
    timeout_s = GLOBAL_CONFIG.resilience.workflow_timeout if GLOBAL_CONFIG.resilience.timeout_enabled else 0
    workflow = create_workflow()
    initial_state = create_initial_state(question, tenant_id)

    logger = get_logger("workflow")
    logger.info("开始执行工作流", question=question, tenant_id=tenant_id, timeout=timeout_s)

    try:
        if timeout_s > 0:
            with timeout_scope(seconds=timeout_s, label="workflow"):
                result = workflow.invoke(initial_state)
        else:
            result = workflow.invoke(initial_state)
        # 三层上下文处理：L1规则删除 → L2 Headroom压缩 → L3 LLM摘要(默认关闭)
        result = GLOBAL_CONTEXT_PIPELINE.process(
            result,
            workflow_id=question[:32],
            model="deepseek-flash",
        )
        return result
    except TimeoutError as e:
        logger.error("工作流执行超时", question=question, timeout=timeout_s)
        return {
            "question": question,
            "tenant_id": tenant_id,
            "error": f"查询超时（超过 {timeout_s} 秒）",
            "errors": [f"工作流执行超时（超过 {timeout_s} 秒）"],
            "status": "timeout",
            "interpretation": {"summary": f"查询超时，请简化问题后重试", "detail": ""},
            "chart_suggestion": {"type": "none", "reason": "超时"},
        }
    except Exception as e:
        logger.error("工作流执行失败", question=question, error=str(e))
        return {
            "question": question,
            "tenant_id": tenant_id,
            "error": f"工作流执行失败: {str(e)}",
            "errors": [f"工作流执行失败: {str(e)}"],
            "status": "error",
            "interpretation": {"summary": "查询处理出错，请重试", "detail": ""},
            "chart_suggestion": {"type": "none", "reason": "错误"},
        }


# 全局工作流实例
GLOBAL_WORKFLOW = create_workflow()