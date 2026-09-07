"""
指数退避重试 - 可配置重试策略，仅重试 transient 异常
"""
from __future__ import annotations
import time
import random
import functools
import threading
from typing import Any, Callable


class RetryableError(Exception):
    """标记为可重试的异常基类"""
    pass


# 常见可重试异常类型
_TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    BlockingIOError,
    RetryableError,
)


def is_retryable(exc: Exception) -> bool:
    """判断异常是否可重试"""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return True
    return any(isinstance(exc, e) for e in _TRANSIENT_EXCEPTIONS)


def retry(
    max_attempts: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 5.0,
    jitter: bool = True,
    retryable_exceptions: tuple = _TRANSIENT_EXCEPTIONS,
):
    """
    指数退避重试装饰器

    参数:
        max_attempts: 最大尝试次数（包含首次）
        base_delay: 初始退避延迟（秒）
        max_delay: 最大退避延迟（秒）
        jitter: 是否添加随机抖动
        retryable_exceptions: 可重试的异常类型元组
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        raise
                    # 计算退避延迟
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)
                    # 记录重试日志
                    _log_retry(func.__name__, attempt, max_attempts, delay, str(e))
                    time.sleep(delay)
                except Exception as e:
                    # 非可重试异常直接透传
                    raise
            # 不应到达这里，但防御性编程
            if last_exc:
                raise last_exc
            return None
        return wrapper
    return decorator


# ============================================================
# Metrics 回调
# ============================================================
_retry_reporter: Callable[[str, int], None] | None = None


def register_retry_reporter(fn: Callable[[str, int], None]):
    global _retry_reporter
    _retry_reporter = fn


def _log_retry(func_name: str, attempt: int, max_attempts: int, delay: float, error: str):
    # 尝试使用结构化日志，不可用时 fallback
    try:
        from observability.logger import get_logger
        logger = get_logger("resilience")
        logger.warning("重试操作",
                       func=func_name,
                       attempt=attempt,
                       max_attempts=max_attempts,
                       delay_ms=round(delay * 1000),
                       error=error)
    except Exception:
        pass
    # 报告 metrics
    if _retry_reporter:
        _retry_reporter(func_name, attempt)