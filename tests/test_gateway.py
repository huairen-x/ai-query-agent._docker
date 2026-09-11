"""MCP HTTP 网关契约与生产加固（B2/B3）"""
import json

from mcp_http_gateway import app

client = app.test_client()


def test_health_lists_six_tables():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert len(resp.get_json()["data_source"]["tables"]) == 6


def test_initialize_returns_instructions_and_version():
    resp = client.post("/message", json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    result = resp.get_json()["result"]
    assert "search_tables" in result["instructions"]
    assert result["serverInfo"]["version"] == "2.1.0"


def test_tools_list_has_nine_tools():
    resp = client.post("/message", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert len(resp.get_json()["result"]["tools"]) == 9


def test_execute_sql_reports_bad_column_as_400():
    resp = client.post("/tools/execute_sql",
                       json={"sql": "SELECT nosuchcol FROM dwd_sale_order_di"})
    assert resp.status_code == 400
    assert "no such column" in resp.get_json()["error"]


def test_audit_limit_is_clamped():
    resp = client.post("/tools/get_audit_logs", json={"limit": -1})
    assert resp.status_code == 200
    assert len(resp.get_json()["result"]["logs"]) <= 20


def test_oversized_body_is_rejected_with_json():
    resp = client.post("/tools/execute_sql",
                       data=b"x" * 8192, content_type="application/json")
    assert resp.status_code == 413
    body = json.loads(resp.get_data(as_text=True))
    assert "error" in body


# ============================================================
# 审计与全链路追踪
# ============================================================

def test_response_carries_trace_header():
    resp = client.post("/tools/search_tables", json={})
    trace_id = resp.headers.get("X-Trace-Id")
    assert trace_id and trace_id != "-"


def test_client_supplied_trace_id_is_reused():
    resp = client.post("/tools/search_tables", json={},
                       headers={"X-Trace-Id": "trace-from-client"})
    assert resp.headers["X-Trace-Id"] == "trace-from-client"


def test_direct_tool_call_is_audited():
    """直连 REST 工具（非工作流）也必须落审计——曾经完全没记录"""
    resp = client.post("/tools/search_tables", json={"keyword": "到店"},
                       headers={"X-Trace-Id": "trace-direct-1"})
    assert resp.status_code == 200

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-direct-1"}).get_json()["result"]["logs"]
    assert len(logs) == 1
    entry = logs[0]
    assert entry["node_name"] == "search_tables"
    assert entry["workflow_step"] == "tool_call"
    assert entry["latency_ms"] >= 0
    assert "到店" in entry["input_data"]
    # 客户端身份可追溯
    assert entry["remote_addr"]


def test_failed_tool_call_is_audited_with_error():
    resp = client.post("/tools/execute_sql",
                       json={"sql": "SELECT nosuchcol FROM dwd_sale_order_di"},
                       headers={"X-Trace-Id": "trace-fail-1"})
    assert resp.status_code == 400

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-fail-1"}).get_json()["result"]["logs"]
    assert len(logs) == 1
    assert "no such column" in logs[0]["error"]


def test_oversized_body_is_audited_as_guard_block():
    resp = client.post("/tools/execute_sql", data=b"x" * 8192,
                       content_type="application/json",
                       headers={"X-Trace-Id": "trace-guard-1"})
    assert resp.status_code == 413
    assert resp.get_json()["error"]

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-guard-1"}).get_json()["result"]["logs"]
    assert len(logs) == 1
    assert logs[0]["node_name"] == "guard:body_limit"
    assert logs[0]["error"]


def test_unsafe_sql_review_is_audited_but_not_rejected():
    """validate_sql 返回 safe=false 仍返回 200（保持原契约），但审计留痕"""
    resp = client.post("/tools/validate_sql",
                       json={"sql": "DROP TABLE dwd_sale_order_di"},
                       headers={"X-Trace-Id": "trace-review-1"})
    assert resp.status_code == 200
    assert resp.get_json()["result"]["safe"] is False

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-review-1"}).get_json()["result"]["logs"]
    assert len(logs) == 1
    assert "drop" in logs[0]["error"].lower()


def test_unknown_tool_is_audited():
    resp = client.post("/tools/nosuchtool", json={},
                       headers={"X-Trace-Id": "trace-unknown-1"})
    assert resp.status_code == 404

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-unknown-1"}).get_json()["result"]["logs"]
    assert len(logs) == 1
    assert logs[0]["error"]


def test_mcp_tool_call_is_audited():
    resp = client.post("/message", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "search_tables", "arguments": {"keyword": "到店"}}},
        headers={"X-Trace-Id": "trace-mcp-1"})
    assert resp.status_code == 200

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-mcp-1"}).get_json()["result"]["logs"]
    assert [entry["node_name"] for entry in logs] == ["search_tables"]


def test_workflow_audit_has_real_latency_and_trace():
    """工作流每个步骤记录真实耗时（曾经全是 0）"""
    resp = client.post("/tools/ask_question",
                       json={"question": "查询最近30天到店量趋势", "tenant_id": "acme"},
                       headers={"X-Trace-Id": "trace-wf-1"})
    assert resp.status_code == 200
    assert resp.get_json()["result"]["trace_id"] == "trace-wf-1"

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-wf-1", "limit": 100}
                       ).get_json()["result"]["logs"]
    steps = [entry for entry in logs if entry["workflow_step"] != "tool_call"]
    assert len(steps) == 7
    # 时间正序，且每个步骤都有真实耗时与租户
    assert all(entry["latency_ms"] > 0 for entry in steps), [e["latency_ms"] for e in steps]
    assert all(entry["tenant_id"] == "acme" for entry in steps)
    timestamps = [entry["created_at"] for entry in steps]
    assert timestamps == sorted(timestamps)


# ============================================================
# 会话聚合与全量 SQL
# ============================================================

def test_client_session_groups_calls_of_one_conversation():
    """同一次对话的多次工具调用共享 client_session，可整组反查"""
    for sql in ("SELECT 1 AS a", "SELECT 2 AS b", "SELECT 3 AS c"):
        resp = client.post("/message?session_id=conv-xyz",
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "execute_sql", "arguments": {"sql": sql}}})
        assert resp.status_code == 200
        assert resp.headers["X-Client-Session"] == "conv-xyz"

    result = client.post("/tools/get_audit_logs",
                         json={"client_session": "conv-xyz", "limit": 100}).get_json()["result"]
    assert result["total"] == 3
    # 时间正序，还原对话内的调用先后
    assert [e["sql_text"] for e in result["logs"]] == ["SELECT 1 AS a", "SELECT 2 AS b", "SELECT 3 AS c"]
    assert all(e["client_session"] == "conv-xyz" for e in result["logs"])


def test_rest_client_session_header_is_honoured():
    resp = client.post("/tools/execute_sql", json={"sql": "SELECT 1 AS a"},
                       headers={"X-Client-Session": "rest-conv-1"})
    assert resp.status_code == 200

    logs = client.post("/tools/get_audit_logs",
                       json={"client_session": "rest-conv-1"}).get_json()["result"]["logs"]
    assert len(logs) == 1
    assert logs[0]["client_session"] == "rest-conv-1"


def test_long_sql_is_stored_in_full():
    """input_data 是 500 字截断预览，sql_text 必须存全量，否则长 SQL 追不回来"""
    sql = "SELECT " + ", ".join(f"col_{i} AS alias_{i}" for i in range(80)) \
          + " FROM dwd_sale_order_di"
    assert len(sql) > 500

    resp = client.post("/tools/execute_sql", json={"sql": sql},
                       headers={"X-Trace-Id": "trace-longsql"})
    assert resp.status_code in (200, 400)

    logs = client.post("/tools/get_audit_logs",
                       json={"trace_id": "trace-longsql"}).get_json()["result"]["logs"]
    assert logs[0]["sql_text"] == sql
    assert len(logs[0]["input_data"]) <= 503  # 预览仍然截断
