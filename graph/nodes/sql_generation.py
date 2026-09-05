"""
Node 4: SQL 生成节点
Mock 规则生成 SQL
"""
from __future__ import annotations
import re
import time
from graph.state import AgentState


def sql_generation_node(state: AgentState) -> dict:
    """SQL 生成节点"""
    start = time.time()
    question = state.get("question", "")
    intent = state.get("intent", "general")
    metadata = state.get("metadata", {})

    # Mock 生成 SQL
    sql = _mock_generate_sql(question, intent)

    return {
        "sql": sql,
        "sql_source": "mock",
        "sql_generation_error": "",
    }


def _mock_generate_sql(question: str, intent: str) -> str:
    """Mock 规则生成 SQL"""
    q = question.lower()

    # 到店量查询
    if "到店" in q or "到访" in q or "visit" in q:
        if "年" in q or "year" in q:
            return ("SELECT t.dt, SUM(t.visit_count) AS total_visits, "
                    "SUM(t.customer_count) AS total_customers\n"
                    "FROM dwd_traffic_visit_di t\n"
                    "WHERE t.dt >= date_sub(current_date(), 365)\n"
                    "GROUP BY t.dt\n"
                    "ORDER BY t.dt")
        elif "月" in q:
            return ("SELECT t.par_month, SUM(t.visit_count) AS total_visits\n"
                    "FROM dwd_traffic_visit_di t\n"
                    "WHERE t.dt >= date_sub(current_date(), 30)\n"
                    "GROUP BY t.par_month\n"
                    "ORDER BY t.par_month")
        else:
            return ("SELECT t.dt, t.store_name, SUM(t.visit_count) AS visit_count\n"
                    "FROM dwd_traffic_visit_di t\n"
                    "WHERE t.dt >= date_sub(current_date(), 7)\n"
                    "GROUP BY t.dt, t.store_name\n"
                    "ORDER BY t.dt DESC\n"
                    "LIMIT 10")

    # 销售金额查询
    if "销售" in q and "金额" in q:
        return ("SELECT t.dt, t.store_code, SUM(t.sale_amount) AS total_amount\n"
                "FROM dwd_sale_order_di t\n"
                "WHERE t.dt >= '2024-01-01'\n"
                "GROUP BY t.dt, t.store_code\n"
                "ORDER BY total_amount DESC\n"
                "LIMIT 10")

    # 产品排名
    if "产品" in q and "排名" in q:
        return ("SELECT t.product_code, t.product_name, SUM(t.sale_qty) AS total_qty\n"
                "FROM dwd_sale_order_di t\n"
                "GROUP BY t.product_code, t.product_name\n"
                "ORDER BY total_qty DESC\n"
                "LIMIT 10")

    # 对比/比较
    if "对比" in q or "比较" in q:
        return ("SELECT t.region, SUM(t.sale_amount) AS total_amount\n"
                "FROM dwd_sale_order_di t\n"
                "WHERE t.dt >= '2024-01-01'\n"
                "GROUP BY t.region\n"
                "ORDER BY total_amount DESC")

    # 默认查询
    return ("SELECT t.dt, COUNT(DISTINCT t.store_code) AS store_count, "
            "SUM(t.sale_amount) AS total_amount\n"
            "FROM dwd_sale_order_di t\n"
            "WHERE t.dt >= '2024-01-01'\n"
            "GROUP BY t.dt\n"
            "ORDER BY t.dt")