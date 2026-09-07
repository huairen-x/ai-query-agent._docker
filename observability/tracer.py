"""
调用链追踪 - 装饰器 + 上下文管理器
用于 LangGraph 节点和关键代码块的耗时追踪
"""
from __future__ import annotations
import time
import functools
from contextlib import contextmanager
from typing import Any, Callable

from observability.metrics import GLOBAL_METRICS, workflow_node_duration_ms, workflow_total


class NodeTracer:
    """节点追踪器 - 记录节点执行耗时和状态"""

    def __init__(self):
        self._spans: list[dict] = []

    @contextmanager
    def trace_span(self, name: str, tags: dict = None):
        """上下文管理器 - 追踪代码块耗时"""
        start = time.monotonic()
        span = {"name": name, "start": start, "tags": tags or {}}
        try:
            yield span
            span["status"] = "success"
        except Exception as e:
            span["status"] = "error"
            span["error"] = str(e)
            raise
        finally:
            span["latency_ms"] = (time.monotonic() - start) * 1000
            self._spans.append(span)
            workflow_node_duration_ms.observe(
                span["latency_ms"],
                node=name,
                status=span.get("status", "unknown"),
            )

    def get_spans(self) -> list[dict]:
        return list(self._spans)

    def clear(self):
        self._spans.clear()


def trace_node(name: str = None):
    """
    装饰器 - 自动追踪 LangGraph 节点函数

    用法:
        @trace_node("sql_generation")
        def sql_generation_node(state: dict) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        node_name = name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            start = time.monotonic()
            try:
                result = func(*args, **kwargs)
                elapsed = (time.monotonic() - start) * 1000
                workflow_node_duration_ms.observe(elapsed, node=node_name, status="success")
                workflow_total.inc(status="success")
                return result
            except Exception as e:
                elapsed = (time.monotonic() - start) * 1000
                workflow_node_duration_ms.observe(elapsed, node=node_name, status="error")
                workflow_total.inc(status="error")
                raise

        return wrapper
    return decorator


@contextmanager
def trace_span(name: str, tags: dict = None):
    """快捷上下文管理器 - 无需创建 NodeTracer 实例"""
    start = time.monotonic()
    try:
        yield
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        workflow_node_duration_ms.observe(elapsed, node=name, status="error")
        workflow_total.inc(status="error")
        raise
    else:
        elapsed = (time.monotonic() - start) * 1000
        workflow_node_duration_ms.observe(elapsed, node=name, status="success")
        workflow_total.inc(status="success")