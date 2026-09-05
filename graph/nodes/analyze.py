"""
Node 2: 需求分析节点
分析用户问题，提取意图、复杂度、关键词
"""
from __future__ import annotations
import re
import time
from graph.state import AgentState


def analyze_node(state: AgentState) -> dict:
    """需求分析节点"""
    start = time.time()
    question = state.get("question", "")

    # 检测意图
    intent = _detect_intent(question)
    # 估算复杂度
    complexity = _estimate_complexity(question)
    # 提取关键词
    keywords = list(_extract_keywords(question))
    # 提取业务标签
    business_tags = _extract_business_tags(question)
    # 是否需要元数据
    needs_metadata = _needs_metadata(question)

    return {
        "intent": intent,
        "complexity": complexity,
        "keywords": keywords,
        "business_tags": business_tags,
        "needs_metadata": needs_metadata,
    }


def _detect_intent(question: str) -> str:
    q = question.lower()
    if any(kw in q for kw in ["对比", "比较", "环比", "同比"]):
        return "comparison"
    elif any(kw in q for kw in ["趋势", "变化", "走势"]):
        return "trend"
    elif any(kw in q for kw in ["占比", "比例", "率"]):
        return "proportion"
    elif any(kw in q for kw in ["排名", "top", "前"]):
        return "ranking"
    elif any(kw in q for kw in ["明细", "详情", "列表"]):
        return "detail"
    elif any(kw in q for kw in ["汇总", "总计", "合计"]):
        return "summary"
    return "general"


def _estimate_complexity(question: str) -> str:
    q = question.lower()
    score = 0
    complex_signals = ["比较", "对比", "占比", "率", "环比", "同比",
                       "窗口", "排名", "累计", "join", "union", "子查询"]
    score += sum(1 for s in complex_signals if s in q)
    if score < 2:
        return "simple"
    elif score < 4:
        return "medium"
    return "complex"


def _extract_keywords(question: str) -> set:
    cn_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', question))
    en_words = set(re.findall(r'[a-zA-Z_]\w{2,}', question))
    return cn_words | en_words


def _extract_business_tags(question: str) -> list:
    tags = []
    patterns = [
        r'(?:近|过去|最近)\s*(\d+)\s*(天|周|月|年)',
        r'(?:按|根据|按照)\s*(\w+)',
        r'(?:的|在|于)\s*(\w+)(?:报告|分析|统计)',
    ]
    for p in patterns:
        matches = re.findall(p, question)
        for m in matches:
            if isinstance(m, tuple):
                tags.extend(m)
            else:
                tags.append(m)
    return tags


def _needs_metadata(question: str) -> bool:
    q = question.lower()
    info_keywords = ["有哪些表", "表结构", "字段", "列", "表名",
                     "有哪些数据", "数据库", "表"]
    return any(kw in q for kw in info_keywords)