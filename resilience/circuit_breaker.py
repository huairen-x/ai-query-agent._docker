"""
熔断器 - 三态滑动窗口熔断
CLOSED → OPEN (失败阈值到达) → HALF_OPEN (恢复超时后) → CLOSED (探针成功)
"""
from __future__ import annotations
import time
import threading
import functools
from enum import IntEnum
from typing import Any, Callable


class CircuitState(IntEnum):
    CLOSED = 0
    HALF_OPEN = 1
    OPEN = 2


class CircuitBreakerOpenError(Exception):
    """熔断器开启时抛出的异常"""
    pass


class CircuitBreaker:
    """三态熔断器 - 滑动窗口计数失败"""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_requests: int = 1,
        window_seconds: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_requests = half_open_max_requests
        self.window_seconds = window_seconds

        self._state = CircuitState.CLOSED
        self._failures: list[float] = []
        self._last_state_change = 0.0
        self._half_open_requests = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def _record_failure(self):
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t < self.window_seconds]
        self._failures.append(now)

    def _failure_count(self) -> int:
        now = time.monotonic()
        self._failures = [t for t in self._failures if now - t < self.window_seconds]
        return len(self._failures)

    def _transition_to(self, new_state: CircuitState):
        self._state = new_state
        self._last_state_change = time.monotonic()
        if new_state == CircuitState.CLOSED:
            self._failures.clear()
            self._half_open_requests = 0

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """执行受熔断保护的调用"""
        with self._lock:
            # OPEN 状态检查是否可进入 HALF_OPEN
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_state_change >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
                else:
                    raise CircuitBreakerOpenError(f"熔断器 [{self.name}] 已开启，请求被拒绝")

            # HALF_OPEN 限制探针请求数
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_requests >= self.half_open_max_requests:
                    raise CircuitBreakerOpenError(f"熔断器 [{self.name}] 半开态，探针请求已满")
                self._half_open_requests += 1

        # 执行调用
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            with self._lock:
                self._record_failure()
                if self._state == CircuitState.HALF_OPEN:
                    self._transition_to(CircuitState.OPEN)
                elif self._failure_count() >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
                # 记录 metrics
                _report_circuit_state(self.name, self._state)
            raise

        # 成功处理
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.CLOSED)
                _report_circuit_state(self.name, self._state)
            else:
                # CLOSED 状态下成功时清空过期记录
                self._failures = [t for t in self._failures
                                  if time.monotonic() - t < self.window_seconds]
        return result

    def __call__(self, func: Callable) -> Callable:
        """装饰器用法"""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)
        return wrapper


# ============================================================
# Metrics 回调（由 metrics 模块注册）
# ============================================================
_circuit_state_reporter: Callable[[str, CircuitState], None] | None = None


def register_state_reporter(fn: Callable[[str, CircuitState], None]):
    global _circuit_state_reporter
    _circuit_state_reporter = fn


def _report_circuit_state(name: str, state: CircuitState):
    if _circuit_state_reporter:
        _circuit_state_reporter(name, state)


# ============================================================
# 装饰器工厂
# ============================================================
def circuit_breaker(name: str = None, **kwargs):
    """
    熔断器装饰器

    用法:
        @circuit_breaker("sqlite", failure_threshold=5)
        def query(...):
            ...
    """
    cb = GLOBAL_CIRCUIT_BREAKERS.get(name)
    if cb is None:
        cb = CircuitBreaker(name=name, **kwargs)
        GLOBAL_CIRCUIT_BREAKERS[name] = cb

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return cb.call(func, *args, **kwargs)
        return wrapper
    return decorator


# 全局熔断器注册表
GLOBAL_CIRCUIT_BREAKERS: dict[str, CircuitBreaker] = {}