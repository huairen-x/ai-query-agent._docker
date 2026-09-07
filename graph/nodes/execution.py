"""
Node 6: 查询执行节点
执行 SQL 查询（Mock 模式）
集成结果缓存
"""
from __future__ import annotations
import time
import random
from graph.state import AgentState
from cache.sqlite_cache import GLOBAL_RESULT_CACHE
from compressor.engine import GLOBAL_HEADROOM
from observability import trace_node
from resilience import circuit_breaker, retry


@trace_node("execution")
def execution_node(state: AgentState) -> dict:
    """查询执行节点"""
    start = time.time()
    sql = state.get("sql", "")
    intent = state.get("intent", "general")

    # 尝试结果缓存
    cached = GLOBAL_RESULT_CACHE.get(sql)
    if cached:
        return {
            "query_result": cached["result"],
            "row_count": cached["row_count"],
            "result_cache_hit": True,
        }

    # Mock 执行
    result = _mock_execute(sql, intent)
    row_count = len(result)

    # 写入缓存
    GLOBAL_RESULT_CACHE.set(sql, {"rows": result, "columns": _get_columns(intent)}, row_count=row_count)

    query_result = {"rows": result, "columns": _get_columns(intent)}
    # 压缩查询结果
    compressed = GLOBAL_HEADROOM.compress("sql_result", query_result, context={"intent": intent})

    return {
        "query_result": compressed.data if GLOBAL_HEADROOM.enabled else query_result,
        "row_count": row_count,
        "result_cache_hit": False,
    }


@circuit_breaker("sql_execution", failure_threshold=5, recovery_timeout=30.0)
@retry(max_attempts=2, base_delay=0.1, max_delay=1.0)
def _mock_execute(sql: str, intent: str) -> list[dict]:
    """Mock 执行 SQL（带熔断保护 + 重试）"""
    now = time.time()
    rows = []

    if "到店" in sql.lower() or "visit" in sql.lower():
        # 到店量数据
        rows = [
            {"dt": "2024-01", "store_name": "北京旗舰店", "visit_count": 15200, "customer_count": 12300},
            {"dt": "2024-02", "store_name": "北京旗舰店", "visit_count": 13800, "customer_count": 11000},
            {"dt": "2024-03", "store_name": "北京旗舰店", "visit_count": 16500, "customer_count": 13400},
            {"dt": "2024-01", "store_name": "上海南京路店", "visit_count": 12800, "customer_count": 10200},
            {"dt": "2024-02", "store_name": "上海南京路店", "visit_count": 11500, "customer_count": 9200},
            {"dt": "2024-03", "store_name": "上海南京路店", "visit_count": 14200, "customer_count": 11500},
        ]
    elif "趋势" in intent or "trend" in sql.lower():
        rows = [
            {"dt": "2024-W01", "total_amount": 1250000, "store_count": 45},
            {"dt": "2024-W02", "total_amount": 1320000, "store_count": 46},
            {"dt": "2024-W03", "total_amount": 1180000, "store_count": 44},
            {"dt": "2024-W04", "total_amount": 1410000, "store_count": 47},
        ]
    elif "排名" in intent or "rank" in sql.lower():
        rows = [
            {"product_code": "P001", "product_name": "经典款T恤", "total_qty": 12500},
            {"product_code": "P002", "product_name": "休闲牛仔裤", "total_qty": 9800},
            {"product_code": "P003", "product_name": "运动鞋", "total_qty": 8700},
            {"product_code": "P004", "product_name": "羽绒服", "total_qty": 6200},
            {"product_code": "P005", "product_name": "帽子", "total_qty": 4500},
        ]
    elif "对比" in intent or "comparison" in intent:
        rows = [
            {"region": "华北", "total_amount": 5680000},
            {"region": "华东", "total_amount": 7230000},
            {"region": "华南", "total_amount": 6120000},
            {"region": "西南", "total_amount": 3850000},
            {"region": "西北", "total_amount": 2160000},
        ]
    else:
        # 默认汇总数据
        rows = [
            {"dt": "2024-Q1", "total_amount": 25600000, "store_count": 156},
            {"dt": "2024-Q2", "total_amount": 28300000, "store_count": 162},
            {"dt": "2024-Q3", "total_amount": 27100000, "store_count": 158},
            {"dt": "2024-Q4", "total_amount": 31200000, "store_count": 170},
        ]

    return rows


def _get_columns(intent: str) -> list[dict]:
    """获取列信息"""
    columns_map = {
        "trend": [
            {"name": "dt", "type": "string", "comment": "日期"},
            {"name": "total_amount", "type": "decimal", "comment": "总金额"},
            {"name": "store_count", "type": "int", "comment": "门店数"},
        ],
        "ranking": [
            {"name": "product_code", "type": "string", "comment": "产品编码"},
            {"name": "product_name", "type": "string", "comment": "产品名称"},
            {"name": "total_qty", "type": "int", "comment": "总销量"},
        ],
        "comparison": [
            {"name": "region", "type": "string", "comment": "区域"},
            {"name": "total_amount", "type": "decimal", "comment": "总金额"},
        ],
        "detail": [
            {"name": "dt", "type": "string", "comment": "日期"},
            {"name": "store_name", "type": "string", "comment": "门店名称"},
            {"name": "visit_count", "type": "int", "comment": "到店数"},
            {"name": "customer_count", "type": "int", "comment": "客户数"},
        ],
    }
    return columns_map.get(intent, columns_map["trend"])