"""
Node 7: 结果解读节点
数据摘要 + 图表建议
"""
from __future__ import annotations
import time
from graph.state import AgentState
from compressor.engine import GLOBAL_HEADROOM


def interpretation_node(state: AgentState) -> dict:
    """结果解读节点"""
    start = time.time()
    query_result = state.get("query_result", {})
    row_count = state.get("row_count", 0)
    intent = state.get("intent", "general")
    question = state.get("question", "")

    rows = query_result.get("rows", []) if isinstance(query_result, dict) else []
    columns = query_result.get("columns", []) if isinstance(query_result, dict) else []

    if not rows:
        return {
            "interpretation": {"summary": "查询未返回数据", "detail": ""},
            "chart_suggestion": {"type": "none", "reason": "无数据"},
        }

    # 生成摘要
    summary = _generate_summary(rows, row_count, intent)
    # 图表建议
    chart = _suggest_chart(intent, rows, columns)

    interpretation = {
        "summary": summary["text"],
        "detail": summary["detail"],
        "row_count": row_count,
        "key_metrics": summary["metrics"],
        "columns": [c["name"] for c in columns],
    }

    return {
        "interpretation": interpretation,
        "chart_suggestion": chart,
    }


def _generate_summary(rows: list, count: int, intent: str) -> dict:
    """生成结果摘要"""
    text = f"查询返回 {count} 条记录。"
    detail = ""
    metrics = {}

    if not rows:
        return {"text": "查询未返回数据", "detail": "", "metrics": {}}

    # 尝试提取数值列
    numeric_cols = []
    if isinstance(rows[0], dict):
        for k, v in rows[0].items():
            if isinstance(v, (int, float)):
                numeric_cols.append(k)

    if numeric_cols:
        # 计算总和
        totals = {}
        for col in numeric_cols:
            totals[col] = sum(row.get(col, 0) or 0 for row in rows)
        metrics["totals"] = totals

        # 最值
        for col in numeric_cols:
            values = [row.get(col, 0) or 0 for row in rows]
            max_val = max(values)
            min_val = min(values)
            max_row = rows[values.index(max_val)]
            min_row = rows[values.index(min_val)]
            metrics[f"{col}_max"] = {"value": max_val, "row": max_row}
            metrics[f"{col}_min"] = {"value": min_val, "row": min_row}

    if intent == "trend":
        text += f" 数据包含 {count} 个时间周期"

        if numeric_cols:
            first_val = rows[0].get(numeric_cols[0], 0) or 0
            last_val = rows[-1].get(numeric_cols[0], 0) or 0
            if last_val > first_val:
                trend = "上升"
            elif last_val < first_val:
                trend = "下降"
            else:
                trend = "持平"
            text += f"，整体趋势{trend}"
            detail = f"从 {rows[0].get('dt', '')} 到 {rows[-1].get('dt', '')}"
            if first_val > 0:
                pct = ((last_val - first_val) / first_val) * 100
                detail += f"，变化幅度 {pct:+.1f}%"

    elif intent == "ranking":
        text += f" 排名前 {count} 的数据"
        if rows:
            detail = f"第一名: {rows[0].get('product_name', rows[0].get('store_name', ''))}"
            detail += f"，最后一名: {rows[-1].get('product_name', rows[-1].get('store_name', ''))}"

    elif intent == "comparison":
        regions = [r.get("region", r.get("store_name", "")) for r in rows]
        text += f" 对比了 {len(regions)} 个维度"
        detail = f"维度: {', '.join(regions[:5])}"

    elif intent == "proportion":
        text += f" 各维度占比分布"
        if numeric_cols:
            total = sum(row.get(numeric_cols[0], 0) or 0 for row in rows)
            if total > 0:
                top = rows[0]
                top_name = top.get("region", top.get("store_name", top.get("product_name", "")))
                top_pct = (top.get(numeric_cols[0], 0) or 0) / total * 100
                detail = f"占比最高: {top_name} ({top_pct:.1f}%)"

    return {"text": text, "detail": detail, "metrics": metrics}


def _suggest_chart(intent: str, rows: list, columns: list) -> dict:
    """建议图表类型"""
    if not rows:
        return {"type": "none", "reason": "无数据"}

    chart_map = {
        "trend": {"type": "line", "reason": "趋势数据适合折线图",
                  "x_axis": "dt", "y_axis": None},
        "comparison": {"type": "bar", "reason": "对比数据适合柱状图",
                       "x_axis": None, "y_axis": None},
        "ranking": {"type": "bar", "reason": "排名数据适合柱状图",
                    "x_axis": None, "y_axis": None},
        "proportion": {"type": "pie", "reason": "占比数据适合饼图",
                       "x_axis": None, "y_axis": None},
        "detail": {"type": "table", "reason": "明细数据适合表格展示"},
        "summary": {"type": "bar", "reason": "汇总数据适合柱状图",
                    "x_axis": None, "y_axis": None},
    }

    suggestion = chart_map.get(intent, {"type": "table", "reason": "默认表格展示"})

    # 自动匹配数值列
    if rows and isinstance(rows[0], dict):
        if suggestion.get("x_axis") is None:
            # 找第一个字符串列作为 x 轴
            for k, v in rows[0].items():
                if isinstance(v, str):
                    suggestion["x_axis"] = k
                    break
        if suggestion.get("y_axis") is None:
            # 找第一个数值列作为 y 轴
            for k, v in rows[0].items():
                if isinstance(v, (int, float)):
                    suggestion["y_axis"] = k
                    break

    return suggestion