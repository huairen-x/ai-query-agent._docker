"""
结构化日志 - JSON 格式输出，支持上下文传递
替换项目中所有 print() 调用
"""
from __future__ import annotations
import json
import sys
import threading
import time
from datetime import datetime, timezone
from typing import IO


class StructuredLogger:
    """结构化日志，输出 JSON 格式到指定流"""

    def __init__(self, name: str = "app", stream: IO = sys.stdout, level: str = "INFO"):
        self.name = name
        self.stream = stream
        self.level = level.upper()
        self._level_map = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        self._local = threading.local()

    def set_request_id(self, request_id: str):
        self._local.request_id = request_id

    def get_request_id(self) -> str:
        return getattr(self._local, "request_id", "")

    def _log(self, level: str, message: str, **extra):
        if self._level_map.get(level, 0) < self._level_map.get(self.level, 1):
            return

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "component": self.name,
            "request_id": self.get_request_id(),
            "message": message,
        }
        if extra:
            filtered = {k: v for k, v in extra.items() if v is not None}
            if filtered:
                record["extra"] = filtered

        self.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.stream.flush()

    def debug(self, message: str, **extra):
        self._log("DEBUG", message, **extra)

    def info(self, message: str, **extra):
        self._log("INFO", message, **extra)

    def warning(self, message: str, **extra):
        self._log("WARNING", message, **extra)

    def error(self, message: str, **extra):
        self._log("ERROR", message, **extra)

    def exception(self, message: str, **extra):
        import traceback
        extra["traceback"] = traceback.format_exc()
        self._log("ERROR", message, **extra)

    def critical(self, message: str, **extra):
        self._log("CRITICAL", message, **extra)


# 全局日志单例
_loggers: dict[str, StructuredLogger] = {}


def get_logger(name: str = "app", level: str = None) -> StructuredLogger:
    """获取或创建命名日志实例"""
    from engine.config import GLOBAL_CONFIG
    if name not in _loggers:
        log_level = level or (GLOBAL_CONFIG.observability.log_level if hasattr(GLOBAL_CONFIG, 'observability') else "INFO")
        _loggers[name] = StructuredLogger(name=name, level=log_level)
    return _loggers[name]