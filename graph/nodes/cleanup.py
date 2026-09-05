"""
Node 1: 上下文清理节点
在 Headroom 压缩前主动移除无效上下文
"""
from __future__ import annotations
import time
from graph.state import AgentState
from compressor.cleanup import GLOBAL_CONTEXT_CLEANER


def cleanup_node(state: AgentState) -> dict:
    """上下文清理节点"""
    start = time.time()

    question = state.get("question", "")
    if not question:
        return {"errors": ["问题为空，无法处理"]}

    # 清理问题文本
    cleaned_question, cleanup_result = GLOBAL_CONTEXT_CLEANER.cleanup_sql(question)
    # 如果是中文问题，不做 SQL 清理，但可以移除多余空格
    cleaned_question = " ".join(question.split())

    # 记录清理统计
    return {
        "question": cleaned_question,
        "cleanup_result": {
            "original_length": len(question),
            "cleaned_length": len(cleaned_question),
            "ratio": len(cleaned_question) / len(question) if question else 1.0,
            "elapsed_ms": (time.time() - start) * 1000,
        },
    }