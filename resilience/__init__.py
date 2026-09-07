"""
弹性工程模块 - 熔断器 + 指数退避重试 + 超时传播 + 降级策略
"""
from resilience.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, circuit_breaker, GLOBAL_CIRCUIT_BREAKERS
from resilience.retry import retry, RetryableError, is_retryable
from resilience.timeout import timeout, timeout_scope, TimeoutError
from resilience.degradation import fallback, graceful, GracefulResult

__all__ = [
    "CircuitBreaker", "CircuitBreakerOpenError", "circuit_breaker", "GLOBAL_CIRCUIT_BREAKERS",
    "retry", "RetryableError", "is_retryable",
    "timeout", "timeout_scope", "TimeoutError",
    "fallback", "graceful", "GracefulResult",
]