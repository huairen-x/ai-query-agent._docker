"""
审计日志 - 结构化的全链路审计

支持：
- 审计日志写入 SQLite
- 会话查询
- 工作流步骤追踪
"""
from __future__ import annotations
import json
import time
import uuid
import logging
from db.manager import GLOBAL_DB_MANAGER

logger = logging.getLogger("audit")


class AuditLogger:
    """
    结构化审计日志
    - 记录每次 MCP 工具调用
    - 记录工作流每个步骤
    - 支持按会话/租户查询
    """

    def __init__(self):
        self._stats = {"total_calls": 0, "errors": 0}

    def log_call(self, tool_name: str, params: dict, result: dict,
                 latency_ms: float, tenant_id: str = "default"):
        """
        记录一次工具调用
        """
        self._stats["total_calls"] += 1
        if "error" in result:
            self._stats["errors"] += 1

        log_entry = {
            "id": str(uuid.uuid4()),
            "session_id": params.get("session_id", str(uuid.uuid4())),
            "tenant_id": tenant_id,
            "question": params.get("question", params.get("sql", "")),
            "workflow_step": tool_name,
            "node_name": tool_name,
            "input_data": json.dumps(params, ensure_ascii=False, default=str)[:500],
            "output_data": json.dumps(result, ensure_ascii=False, default=str)[:500],
            "token_estimate": 0,
            "compression_ratio": 1.0,
            "latency_ms": round(latency_ms, 1),
            "cache_hit": 1 if result.get("cached") else 0,
            "error": result.get("error", ""),
            "created_at": time.time(),
        }

        try:
            GLOBAL_DB_MANAGER.insert("audit_logs", log_entry)
        except Exception as e:
            logger.error(f"写入审计日志失败: {e}")

    def log_workflow_step(self, session_id: str, step_name: str, step_label: str,
                          input_data: dict, output_data: dict,
                          tenant_id: str = "default"):
        """记录工作流步骤"""
        log_entry = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "tenant_id": tenant_id,
            "question": input_data.get("question", ""),
            "workflow_step": step_name,
            "node_name": step_label,
            "input_data": json.dumps(input_data, ensure_ascii=False, default=str)[:500],
            "output_data": json.dumps(output_data, ensure_ascii=False, default=str)[:500],
            "token_estimate": 0,
            "compression_ratio": 1.0,
            "latency_ms": 0,
            "cache_hit": 0,
            "error": "",
            "created_at": time.time(),
        }
        try:
            GLOBAL_DB_MANAGER.insert("audit_logs", log_entry)
        except Exception:
            pass

    def get_session_logs(self, session_id: str, limit: int = 50) -> list:
        """获取会话的审计日志"""
        try:
            return GLOBAL_DB_MANAGER.fetch_all(
                "SELECT * FROM audit_logs WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
                (session_id, limit)
            )
        except Exception:
            return []

    def get_stats(self) -> dict:
        return dict(self._stats)


GLOBAL_AUDIT_LOGGER = AuditLogger()