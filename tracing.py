"""
tracing.py — 全链路追踪（trace_id）与结构化日志

设计：
    - 每个 HTTP/MCP 请求由网关分配 trace_id，存入 contextvar；
      网关、工作流节点等深层代码无需透传参数即可取到它。
    - 所有日志行由 logging.Filter 注入 trace_id，从而串成一条完整链路。
    - trace_id 同时写入 audit_logs.trace_id 并随响应头 X-Trace-Id 返回客户端，
      可由 get_audit_logs(trace_id=...) 反查该次调用的全部步骤。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
import uuid

TRACE_HEADER = "X-Trace-Id"
# 客户端会话标识请求头（REST 路径用；MCP 路径取自 ?session_id=）
CLIENT_SESSION_HEADER = "X-Client-Session"

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")
_client_session: contextvars.ContextVar[str] = contextvars.ContextVar("client_session", default="")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def set_trace_id(value: str | None = None) -> str:
    """设置当前上下文的 trace_id；不传则新生成"""
    trace_id = value or new_trace_id()
    _trace_id.set(trace_id)
    return trace_id


def get_trace_id() -> str:
    return _trace_id.get()


def set_client_session(value: str | None) -> str:
    """设置客户端会话标识（MCP 的 SSE session_id / REST 的 X-Client-Session）。

    trace_id 标识**一次调用**，client_session 标识**一段会话**；
    同一次对话的多次工具调用共享 client_session，据此可在审计里整组捞出。
    """
    session = value or ""
    _client_session.set(session)
    return session


def get_client_session() -> str:
    return _client_session.get()


def current_or_new_trace_id() -> str:
    """取当前 trace_id；上下文里没有（"-"）时新生成并写入上下文"""
    trace_id = _trace_id.get()
    return trace_id if trace_id != "-" else set_trace_id()


def preview(value, max_len: int = 500) -> str:
    """审计/日志用的 JSON 预览（超长截断）"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= max_len else text[:max_len] + "..."


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中文 1.5/字，其余 0.25/字"""
    cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return int(cn_chars * 1.5 + (len(text) - cn_chars) * 0.25)


class _TraceFilter(logging.Filter):
    """给每条日志记录补 trace_id（以及 text 格式用的 detail 默认值）"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get()
        if not hasattr(record, "detail"):
            record.detail = ""
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "trace_id": getattr(record, "trace_id", "-"),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_logging_ready = False


def setup_logging() -> None:
    """装载根 logger（幂等）：日志带 trace_id，输出到 stdout → journald"""
    global _logging_ready
    if _logging_ready:
        return
    _logging_ready = True

    level_name = os.environ.get("AGENT_LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.addFilter(_TraceFilter())
        if os.environ.get("AGENT_LOG_JSON", "false").lower() == "true":
            handler.setFormatter(_JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s [%(trace_id)s] %(name)s: %(message)s%(detail)s"
                )
            )
        root.addHandler(handler)
    root.setLevel(getattr(logging, level_name, logging.INFO))


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    """结构化事件日志：text 模式追加 k=v，JSON 模式保留字段"""
    detail = "".join(f" {k}={v}" for k, v in fields.items() if v is not None)
    logging.getLogger("event").log(
        level, event, extra={"fields": fields, "detail": detail}
    )
