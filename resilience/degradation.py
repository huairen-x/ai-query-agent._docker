"""
降级策略 - fallback 装饰器，异常时返回默认值或降级函数
"""
from __future__ import annotations
import functools
import traceback
from typing import Any, Callable, Optional
from dataclasses import dataclass


@dataclass
class GracefulResult:
    """优雅降级的结果包装"""
    success: bool
    data: Any = None
    error: Optional[str] = None


def fallback(result: Any = None, fn: Callable = None):
    """
    降级装饰器 - 异常时返回默认值或调用降级函数

    用法:
        @fallback(result=[])
        def query(...):
            ...

        @fallback(fn=lambda: {"error": "服务不可用"})
        def query(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # 记录降级日志
                try:
                    from observability.logger import get_logger
                    logger = get_logger("resilience")
                    logger.warning("降级触发",
                                   func=func.__name__,
                                   error=str(e),
                                   fallback_type="result" if fn is None else "function")
                except Exception:
                    pass
                if fn is not None:
                    return fn(*args, **kwargs)
                return result
        return wrapper
    return decorator


def graceful(func: Callable) -> Callable:
    """
    优雅执行装饰器 - 将异常包装为 GracefulResult

    用法:
        @graceful
        def query(...) -> GracefulResult:
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            data = func(*args, **kwargs)
            return GracefulResult(success=True, data=data)
        except Exception as e:
            return GracefulResult(success=False, error=str(e))
    return wrapper