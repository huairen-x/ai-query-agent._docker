"""
速率限制中间件 - 基于令牌桶算法（in-memory）
每个客户端 IP 独立计数
"""
from __future__ import annotations
import time
import threading
from functools import wraps
from flask import request, jsonify
from engine.config import GLOBAL_CONFIG


class TokenBucket:
    """令牌桶"""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # 每秒填充令牌数
        self.capacity = capacity  # 桶容量
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        with self._lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False


class RateLimiter:
    """全局速率限制器"""

    def __init__(self):
        cfg = GLOBAL_CONFIG.security.rate_limit
        self.enabled = cfg.enabled
        self.global_bucket = TokenBucket(cfg.global_rps, cfg.global_burst)
        self.client_buckets: dict[str, TokenBucket] = {}
        self.client_rps = cfg.client_rps
        self.client_burst = cfg.client_burst
        self._lock = threading.Lock()

    def check(self, client_ip: str = None) -> bool:
        if not self.enabled:
            return True

        # 全局限制
        if not self.global_bucket.consume():
            return False

        # 客户端限制
        if client_ip:
            with self._lock:
                if client_ip not in self.client_buckets:
                    self.client_buckets[client_ip] = TokenBucket(self.client_rps, self.client_burst)
                if not self.client_buckets[client_ip].consume():
                    return False

        return True

    def cleanup(self, max_age: float = 300.0):
        """清理过期客户端桶"""
        now = time.monotonic()
        with self._lock:
            expired = [ip for ip, bucket in self.client_buckets.items()
                       if now - bucket.last_refill > max_age]
            for ip in expired:
                del self.client_buckets[ip]


GLOBAL_RATE_LIMITER = RateLimiter()


def rate_limit(f):
    """装饰器：对请求进行速率限制"""

    @wraps(f)
    def decorated(*args, **kwargs):
        cfg = GLOBAL_CONFIG.security.rate_limit
        if not cfg.enabled:
            return f(*args, **kwargs)

        client_ip = request.remote_addr or "unknown"
        if not GLOBAL_RATE_LIMITER.check(client_ip):
            return jsonify({"error": "Too Many Requests"}), 429

        return f(*args, **kwargs)

    return decorated