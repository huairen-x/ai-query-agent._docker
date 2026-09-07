"""
CORS 配置中间件
支持白名单域名限制，不再全开
"""
from __future__ import annotations
from flask import Flask
from engine.config import GLOBAL_CONFIG


def configure_cors(app: Flask):
    """
    根据配置设置 CORS 策略
    如果 allowed_origins 包含 "*" 则允许所有（不推荐生产使用）
    """
    cfg = GLOBAL_CONFIG.security.cors

    @app.after_request
    def add_cors_headers(response):
        origin = response.request.headers.get("Origin", "")

        if cfg.allowed_origins == ["*"]:
            # 开发模式
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin in cfg.allowed_origins:
            # 精确匹配白名单
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        else:
            # 不在白名单中，不设置 CORS 头
            return response

        response.headers["Access-Control-Allow-Methods"] = ", ".join(cfg.allowed_methods)
        response.headers["Access-Control-Allow-Headers"] = ", ".join(cfg.allowed_headers)
        response.headers["Access-Control-Max-Age"] = str(cfg.max_age)

        if cfg.allow_credentials:
            response.headers["Access-Control-Allow-Credentials"] = "true"

        return response