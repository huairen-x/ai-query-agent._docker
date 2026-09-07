"""
API Key 认证中间件
支持多密钥配置，通过请求头 X-API-Key 传递
"""
from __future__ import annotations
import hmac
from functools import wraps
from flask import request, jsonify
from engine.config import GLOBAL_CONFIG


def require_api_key(f):
    """装饰器：要求请求携带有效的 API Key"""

    @wraps(f)
    def decorated(*args, **kwargs):
        cfg = GLOBAL_CONFIG.security
        if not cfg.enabled:
            return f(*args, **kwargs)

        # 健康检查端点不需要认证
        if request.path == "/health":
            return f(*args, **kwargs)

        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            return jsonify({"error": "Missing X-API-Key header"}), 401

        # 使用 constant-time comparison 防止时序攻击
        for valid_key in cfg.api_keys:
            if hmac.compare_digest(api_key, valid_key):
                return f(*args, **kwargs)

        return jsonify({"error": "Invalid API Key"}), 403

    return decorated