"""
Node 3: 元数据查询节点
查询表结构、字段信息、搜索相关表
集成 SQLite 缓存
"""
from __future__ import annotations
import time
from graph.state import AgentState
from cache.sqlite_cache import GLOBAL_METADATA_CACHE
from compressor.engine import GLOBAL_HEADROOM
from observability import trace_node


@trace_node("metadata")
def metadata_node(state: AgentState) -> dict:
    """元数据查询节点"""
    start = time.time()
    question = state.get("question", "")
    keywords = state.get("keywords", [])
    intent = state.get("intent", "general")

    # 1. 搜索相关表
    related_tables = _search_tables(question, keywords)

    # 2. 查询表元数据
    metadata_info = {}
    metadata_cache_hit = False

    for table in related_tables:
        table_name = table["table_name"]
        cached = GLOBAL_METADATA_CACHE.get("describe_table", table_name=table_name)
        if cached:
            metadata_info[table_name] = cached
            metadata_cache_hit = True
        else:
            meta = _mock_describe_table(table_name)
            GLOBAL_METADATA_CACHE.set(meta, "describe_table", table_name=table_name)
            metadata_info[table_name] = meta

    # 3. 压缩元数据
    compressed = GLOBAL_HEADROOM.compress("metadata", metadata_info, context={"question": question})

    return {
        "metadata": compressed.data if GLOBAL_HEADROOM.enabled else metadata_info,
        "metadata_cache_hit": metadata_cache_hit,
        "related_tables": related_tables,
    }


def _search_tables(question: str, keywords: list) -> list:
    """搜索相关表"""
    # Mock 数据
    all_tables = [
        {"table_name": "dwd_sale_order_di", "comment": "销售订单明细"},
        {"table_name": "dwd_sale_order_item_di", "comment": "销售订单行项目"},
        {"table_name": "dim_product_df", "comment": "产品维度表"},
        {"table_name": "dim_store_df", "comment": "门店维度表"},
        {"table_name": "dwd_traffic_visit_di", "comment": "到店流量明细"},
        {"table_name": "dwd_customer_visit_di", "comment": "客户到访明细"},
    ]

    q = question.lower()
    matched = []
    for t in all_tables:
        name = t["table_name"].lower()
        comment = t["comment"].lower()
        # 匹配表名或注释
        if any(kw.lower() in name or kw.lower() in comment for kw in keywords):
            matched.append(t)
        # 特殊匹配
        if "到店" in q and ("traffic" in name or "visit" in name or "customer" in name):
            if t not in matched:
                matched.append(t)
        if "销售" in q or "金额" in q or "订单" in q:
            if "sale" in name or "order" in name:
                if t not in matched:
                    matched.append(t)

    return matched[:5] if matched else all_tables[:3]


def _mock_describe_table(table_name: str) -> dict:
    """模拟描述表结构"""
    return {
        "table_name": table_name,
        "columns": [
            {"name": "id", "type": "bigint", "comment": "主键ID"},
            {"name": "dt", "type": "string", "comment": "分区日期"},
            {"name": "par_month", "type": "string", "comment": "分区月份"},
            {"name": "sale_amount", "type": "decimal(18,2)", "comment": "销售金额"},
            {"name": "sale_qty", "type": "int", "comment": "销售数量"},
            {"name": "product_code", "type": "string", "comment": "产品编码"},
            {"name": "product_name", "type": "string", "comment": "产品名称"},
            {"name": "store_code", "type": "string", "comment": "门店编码"},
            {"name": "store_name", "type": "string", "comment": "门店名称"},
            {"name": "region", "type": "string", "comment": "区域"},
            {"name": "visit_count", "type": "int", "comment": "到店数量"},
            {"name": "customer_count", "type": "int", "comment": "客户数量"},
            {"name": "create_time", "type": "timestamp", "comment": "创建时间"},
            {"name": "update_time", "type": "timestamp", "comment": "更新时间"},
        ],
        "partition_keys": ["dt", "par_month"],
        "table_type": "MANAGED_TABLE",
        "total_size_gb": 12.5,
    }