"""
Node 6: 查询执行节点
对 Mock 数仓真实执行 SQL（SQLite 承载 Hive 风格表）
集成结果缓存
"""
from __future__ import annotations
import os
import time
from graph.state import AgentState
from cache.sqlite_cache import GLOBAL_RESULT_CACHE
from compressor.engine import GLOBAL_HEADROOM
from datasource.mock_warehouse import GLOBAL_WAREHOUSE, WarehouseError

_DEBUG = os.environ.get("HEADROOM_DEBUG", "true").lower() == "true"


def _bump(state: AgentState, name: str) -> dict:
    """累加 cache_hits，LangGraph 的 key 是整体替换，必须返回完整字典"""
    hits = dict(state.get("cache_hits", {}))
    hits[name] = hits.get(name, 0) + 1
    return hits


def execution_node(state: AgentState) -> dict:
    """查询执行节点"""
    start = time.time()
    sql = state.get("sql", "")
    intent = state.get("intent", "")

    # 尝试结果缓存
    cached = GLOBAL_RESULT_CACHE.get(sql)
    if cached:
        return {
            "query_result": {"rows": cached["rows"], "columns": cached["columns"]},
            "row_count": cached["row_count"],
            "result_cache_hit": True,
            "cache_hits": _bump(state, "result_cache"),
        }

    try:
        executed = GLOBAL_WAREHOUSE.execute(sql)
    except WarehouseError as exc:
        if _DEBUG:
            print(f"[node:execution] SQL FAIL {exc}", flush=True)
        return {
            "query_result": {"rows": [], "columns": [], "error": str(exc)},
            "row_count": 0,
            "result_cache_hit": False,
            "errors": state.get("errors", []) + [f"查询执行失败: {exc}"],
        }

    rows = executed["rows"]
    columns = executed["columns"]
    query_result = {"rows": rows, "columns": columns}
    if executed["truncated"]:
        query_result["truncated"] = True

    # 写入缓存（只缓存成功结果）
    GLOBAL_RESULT_CACHE.set(sql, {"rows": rows, "columns": columns}, row_count=len(rows))

    if _DEBUG:
        print(f"[node:execution] compress input rows={len(rows)}", flush=True)
    compressed = GLOBAL_HEADROOM.compress("sql_result", query_result, context={"intent": intent})
    if _DEBUG:
        print(f"[node:execution] compress ratio={compressed.ratio} saved={compressed.tokens_saved} strat={compressed.strategy}", flush=True)

    return {
        "query_result": compressed.data if GLOBAL_HEADROOM.enabled else query_result,
        "row_count": len(rows),
        "result_cache_hit": False,
        "query_elapsed_ms": round((time.time() - start) * 1000, 2),
    }
