"""
超时传播 - 装饰器 + 上下文管理器
基于 threading.Timer（跨平台兼容）
"""
from __future__ import annotations
import time
import threading
import functools
from contextlib import contextmanager
from typing import Any, Callable, Generator


class TimeoutError(Exception):
    """操作超时异常"""
    pass


class _TimeoutGuard:
    """超时守卫 - 使用 Timer 中断"""

    def __init__(self, seconds: float, label: str = ""):
        self.seconds = seconds
        self.label = label
        self._timer: threading.Timer | None = None
        self._timed_out = False

    def __enter__(self):
        if self.seconds <= 0:
            return self
        self._timer = threading.Timer(self.seconds, self._timeout_handler)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._timed_out:
            raise TimeoutError(f"操作超时 [{self.label}]: {self.seconds}秒")
        return False

    def _timeout_handler(self):
        self._timed_out = True


@contextmanager
def timeout_scope(seconds: float, label: str = "") -> Generator:
    """超时上下文管理器"""
    if seconds <= 0:
        yield
        return
    guard = _TimeoutGuard(seconds, label)
    with guard:
        yield


def timeout(seconds: float = 30.0, label: str = None):
    """
    超时装饰器

    用法:
        @timeout(seconds=10, label="SQL查询")
        def query():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            fn_label = label or func.__name__
            result = []
            error = []
            done = threading.Event()

            def target():
                try:
                    r = func(*args, **kwargs)
                    result.append(r)
                except Exception as e:
                    error.append(e)
                finally:
                    done.set()

            t = threading.Thread(target=target, daemon=True)
            t.start()
            if not done.wait(timeout=seconds):
                raise TimeoutError(f"操作超时 [{fn_label}]: {seconds}秒")
            if error:
                raise error[0]
            return result[0]
        return wrapper
    return decorator