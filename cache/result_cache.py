"""
结果缓存 - 缓存 SQL 查询结果
针对相同 SQL 的重复查询直接返回缓存结果
"""
from __future__ import annotations
import time
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Optional
from collections import OrderedDict

from engine.config import GLOBAL_CONFIG


@dataclass
class ResultEntry:
    """结果缓存条目"""
    sql_hash: str
    sql: str
    result: any
    row_count: int = 0
    created_at: float = 0.0
    expires_at: float = 0.0
    hit_count: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class ResultCache:
    """
    SQL 查询结果缓存
    - 精确 SQL 匹配（hash）
    - 短 TTL（数据时效性）
    - LRU 淘汰
    """

    def __init__(self):
        config = GLOBAL_CONFIG.cache
        self.enabled = config.result_cache_enabled
        self.ttl = config.result_cache_ttl
        self.max_entries = 2000

        self._entries: dict[str, ResultEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = {"hits": 0, "misses": 0}

    def _normalize_sql(self, sql: str) -> str:
        """SQL 标准化（去除空格、大小写统一）"""
        import re
        sql = re.sub(r'\s+', ' ', sql.strip())
        return sql.lower()

    def _make_hash(self, sql: str) -> str:
        return hashlib.md5(self._normalize_sql(sql).encode()).hexdigest()

    def get(self, sql: str) -> Optional[any]:
        if not self.enabled:
            return None

        with self._lock:
            h = self._make_hash(sql)
            entry = self._entries.get(h)

            if entry and not entry.is_expired:
                entry.hit_count += 1
                self._entries.move_to_end(h)
                self._stats["hits"] += 1
                return entry.result

            self._stats["misses"] += 1
            return None

    def set(self, sql: str, result: any, row_count: int = 0, ttl: int = None) -> str:
        if not self.enabled:
            return ""

        with self._lock:
            h = self._make_hash(sql)
            now = time.time()

            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)

            entry = ResultEntry(
                sql_hash=h,
                sql=sql,
                result=result,
                row_count=row_count,
                created_at=now,
                expires_at=now + (ttl or self.ttl),
            )
            self._entries[h] = entry
            return h

    def invalidate(self, sql: str = None):
        with self._lock:
            if sql:
                h = self._make_hash(sql)
                self._entries.pop(h, None)
            else:
                self._entries.clear()

    def clear(self):
        self.invalidate()
        self._stats = {"hits": 0, "misses": 0}

    def get_stats(self) -> dict:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "hit_rate": round(self._stats["hits"] / total, 4) if total else 0,
                "entries": len(self._entries),
            }


GLOBAL_RESULT_CACHE = ResultCache()