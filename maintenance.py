"""后台维护：过期缓存与审计日志回收"""
from __future__ import annotations
import os
import threading
import time

from db.manager import GLOBAL_DB_MANAGER


def _loop(interval: int, retention_days: int) -> None:
    while True:
        time.sleep(interval)
        try:
            expired = GLOBAL_DB_MANAGER.cleanup()
            purged = GLOBAL_DB_MANAGER.purge_audit(retention_days)
            print(f"[maintenance] 清理过期缓存 {expired} 条，回收审计 {purged} 条"
                  f"（保留 {retention_days} 天）", flush=True)
        except Exception as exc:
            print(f"[maintenance] 失败: {type(exc).__name__}: {exc}", flush=True)


def start() -> threading.Thread | None:
    """启动后台维护线程；CACHE_CLEANUP_INTERVAL_SEC<=0 时禁用"""
    interval = int(os.environ.get("CACHE_CLEANUP_INTERVAL_SEC", "300"))
    if interval <= 0:
        print("[maintenance] 已禁用（CACHE_CLEANUP_INTERVAL_SEC<=0）", flush=True)
        return None
    retention_days = int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))
    thread = threading.Thread(target=_loop, args=(interval, retention_days),
                              name="maintenance", daemon=True)
    thread.start()
    return thread
