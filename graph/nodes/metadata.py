"""
Node 3: 元数据查询节点
查询表结构、字段信息、搜索相关表
集成 SQLite 缓存
"""
from __future__ import annotations
import os
import re
import time
from graph.state import AgentState
from cache.sqlite_cache import GLOBAL_METADATA_CACHE
from compressor.engine import GLOBAL_HEADROOM
from datasource.mock_warehouse import CATALOG, GLOBAL_WAREHOUSE, WarehouseError

# 表名 + 注释组成的检索底表，只构建一次
_TABLE_HAYSTACK = {
    name: f"{name} {table['comment']}".lower()
    for name, table in CATALOG.items()
}


def metadata_node(state: AgentState) -> dict:
    """元数据查询节点"""
    start = time.time()
    question = state.get("question", "")
    keywords = state.get("keywords", [])

    # 1. 搜索相关表
    related_tables = _search_tables(question, keywords)

    # 2. 查询表元数据（带缓存）
    metadata_info = {}
    metadata_cache_hit = False

    for table in related_tables:
        table_name = table["table_name"]
        cached = GLOBAL_METADATA_CACHE.get("describe_table", table_name=table_name)
        if cached:
            metadata_info[table_name] = cached
            metadata_cache_hit = True
            continue
        try:
            meta = GLOBAL_WAREHOUSE.describe(table_name)
        except WarehouseError as exc:
            print(f"[node:metadata] describe {table_name} FAIL {exc}", flush=True)
            continue
        GLOBAL_METADATA_CACHE.set(meta, "describe_table", table_name=table_name)
        metadata_info[table_name] = meta

    # 3. 压缩元数据
    if os.environ.get("HEADROOM_DEBUG", "true").lower() == "true":
        print(f"[node:metadata] compress input tables={len(metadata_info)}", flush=True)
    compressed = GLOBAL_HEADROOM.compress("metadata", metadata_info, context={"question": question})
    if os.environ.get("HEADROOM_DEBUG", "true").lower() == "true":
        print(f"[node:metadata] compress ratio={compressed.ratio} saved={compressed.tokens_saved} strat={compressed.strategy}", flush=True)

    return {
        "metadata": compressed.data if GLOBAL_HEADROOM.enabled else metadata_info,
        "metadata_cache_hit": metadata_cache_hit,
        "related_tables": related_tables,
        "cache_hits": _bump(state, "metadata_cache") if metadata_cache_hit else state.get("cache_hits", {}),
    }


def _bump(state: AgentState, name: str) -> dict:
    """累加 cache_hits，LangGraph 的 key 是整体替换，必须返回完整字典"""
    hits = dict(state.get("cache_hits", {}))
    hits[name] = hits.get(name, 0) + 1
    return hits


def _search_tables(question: str, keywords: list) -> list:
    """
    按问题/关键词与「表名 + 表注释」的字符重合度排序选表。

    中文问题经 analyze 分词后往往是一整段（如 "查询最近"），朴素子串匹配会全军覆没，
    因此这里用 2-gram 重合度打分："到店量趋势" → "到店" 命中 dwd_traffic_visit_di。
    """
    grams = _grams(question)
    for keyword in keywords:
        grams |= _grams(str(keyword))

    scored = []
    for table_name, haystack in _TABLE_HAYSTACK.items():
        score = sum(1 for gram in grams if gram in haystack)
        if score:
            scored.append((score, table_name))

    if not scored:
        return GLOBAL_WAREHOUSE.list_tables()[:3]

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"table_name": name, "comment": CATALOG[name]["comment"]}
        for _score, name in scored[:5]
    ]


def _grams(text: str) -> set[str]:
    """中文取 2-gram，英文/数字取长度 ≥2 的词"""
    grams = set(re.findall(r"[a-zA-Z_]\w{1,}", text.lower()))
    cn_runs = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cn_runs:
        grams.update(run[i:i + 2] for i in range(len(run) - 1))
        grams.add(run)
    return grams
