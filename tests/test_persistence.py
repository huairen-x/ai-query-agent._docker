"""持久化语义：工作流提交、缓存读写、并发写入、审计回收"""
import threading
import time
import uuid

from db.manager import GLOBAL_DB_MANAGER
from cache.sqlite_cache import GLOBAL_RESULT_CACHE, GLOBAL_METADATA_CACHE
from graph.workflow import run_workflow


def test_workflow_persists_audit_and_session():
    state = run_workflow("查询最近30天到店量趋势")
    session_id = state["session_id"]

    assert GLOBAL_DB_MANAGER.count("audit_logs") > 0
    assert GLOBAL_DB_MANAGER.count(
        "sessions", "session_id = ?", (session_id,)) == 1


def test_result_cache_roundtrip():
    sql = f"SELECT {uuid.uuid4().hex} FROM dwd_sale_order_di"
    GLOBAL_RESULT_CACHE.set(
        sql,
        {"rows": [{"a": 1}], "columns": [{"name": "a", "type": "int", "comment": ""}]},
        row_count=1,
    )
    cached = GLOBAL_RESULT_CACHE.get(sql)
    assert cached["rows"] == [{"a": 1}]
    assert cached["row_count"] == 1


def test_metadata_cache_roundtrip():
    table = uuid.uuid4().hex
    GLOBAL_METADATA_CACHE.set({"x": 1}, "describe_table", table_name=table)
    assert GLOBAL_METADATA_CACHE.get("describe_table", table_name=table) == {"x": 1}


def test_concurrent_writes_do_not_lock():
    errors = []

    def writer(tag):
        try:
            for i in range(20):
                GLOBAL_METADATA_CACHE.set(
                    {"i": i}, "describe_table", table_name=f"{tag}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("t1", "t2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert GLOBAL_DB_MANAGER.count("metadata_cache") >= 40


def test_purge_audit_removes_expired_rows():
    row_id = uuid.uuid4().hex
    GLOBAL_DB_MANAGER.insert("audit_logs", {
        "id": row_id,
        "session_id": "purge-test",
        "question": "q",
        "workflow_step": "step",
        "node_name": "node",
        "created_at": time.time() - 200 * 86400,
    })
    purged = GLOBAL_DB_MANAGER.purge_audit(90)
    assert purged >= 1
    assert GLOBAL_DB_MANAGER.fetch_one(
        "SELECT id FROM audit_logs WHERE id = ?", (row_id,)) is None
