"""
AgentState - LangGraph 状态定义
"""
from __future__ import annotations
from typing import TypedDict, List, Optional, Dict, Any
import time
import uuid

import tracing


def new_session_id() -> str:
    return str(uuid.uuid4())


class AgentState(TypedDict, total=False):
    # ===== 基础信息 =====
    question: str                          # 用户原始问题
    session_id: str                        # 会话 ID
    tenant_id: str                         # 租户 ID
    trace_id: str                          # 全链路追踪 ID（审计与日志共用）

    # ===== 第1步: 上下文清理 =====
    cleanup_result: dict                   # 清理结果统计

    # ===== 第2步: 需求分析 =====
    intent: str                            # 查询意图
    complexity: str                        # 复杂度
    keywords: List[str]                    # 关键词
    business_tags: List[str]               # 业务标签
    needs_metadata: bool                   # 是否需要查元数据

    # ===== 第3步: 元数据查询 =====
    metadata: dict                         # 查询到的元数据
    metadata_cache_hit: bool               # 元数据缓存是否命中
    related_tables: List[dict]             # 相关表列表

    # ===== 第4步: SQL 生成 =====
    sql: str                               # 生成的 SQL
    sql_source: str                        # SQL 来源 (mock/template)
    sql_generation_error: str              # SQL 生成错误

    # ===== 第5步: SQL 审查 =====
    validation: dict                       # 验证结果
    validation_passed: bool                # 是否通过
    validation_attempts: int               # 重试次数

    # ===== 第6步: 查询执行 =====
    query_result: dict                     # 查询结果
    row_count: int                         # 结果行数
    result_cache_hit: bool                 # 结果缓存是否命中
    query_elapsed_ms: float                # 查询耗时

    # ===== 第7步: 结果解读 =====
    interpretation: dict                   # 结果解读
    chart_suggestion: dict                 # 图表建议

    # ===== 第8步: 审计 =====
    audit_trail: List[dict]                # 审计日志
    node_timings: Dict[str, dict]          # 各节点真实耗时（由 workflow 包裹器写入）
    cache_hits: Dict[str, int]             # 各缓存命中统计
    errors: List[str]                      # 错误列表
    started_at: float                      # 开始时间戳
    completed_at: float                    # 完成时间戳
    total_latency_ms: float                # 总耗时


def create_initial_state(
    question: str, tenant_id: str = "default", trace_id: str | None = None
) -> AgentState:
    """创建初始状态；trace_id 缺省时沿用当前上下文（无则新建）"""
    return {
        "question": question,
        "session_id": new_session_id(),
        "tenant_id": tenant_id,
        "trace_id": trace_id or tracing.current_or_new_trace_id(),
        "intent": "",
        "complexity": "simple",
        "keywords": [],
        "business_tags": [],
        "needs_metadata": False,
        "metadata": {},
        "metadata_cache_hit": False,
        "related_tables": [],
        "sql": "",
        "sql_source": "",
        "sql_generation_error": "",
        "validation": {},
        "validation_passed": False,
        "validation_attempts": 0,
        "query_result": {},
        "row_count": 0,
        "result_cache_hit": False,
        "query_elapsed_ms": 0.0,
        "interpretation": {},
        "chart_suggestion": {},
        "audit_trail": [],
        "node_timings": {},
        "cache_hits": {},
        "errors": [],
        "started_at": time.time(),
        "completed_at": 0.0,
        "total_latency_ms": 0.0,
    }