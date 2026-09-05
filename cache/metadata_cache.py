"""
元数据缓存 - 缓存表结构、字段信息、分区信息
避免重复查询 Hive MetaStore
"""
from __future__ import annotations
import time
import threading
from dataclasses import dataclass, field
from typing import Optional
from collections import OrderedDict

from engine.config import GLOBAL_CONFIG


@dataclass
class MetadataEntry:
    """元数据缓存条目"""
    key: str
    data: any
    created_at: float = 0.0
    expires_at: float = 0.0
    hit_count: int = 0
    data_size_bytes: int = 0


class MetadataCache:
    """
    元数据缓存
    - 缓存表列表、表结构、字段搜索等
    - TTL 过期
    - LRU 淘汰
    - 主动失效（检测 DDL 变更）
    """

    def __init__(self):
        config = GLOBAL_CONFIG.cache
        self.enabled = config.metadata_cache_enabled
        self.ttl = config.metadata_cache_ttl
        self.max_entries = 5000

        self._entries: dict[str, MetadataEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = {"hits": 0, "misses": 0}

    def _make_key(self, tool: str, **params) -> str:
        parts = [tool]
        for k, v in sorted(params.items()):
            parts.append(f"{k}={v}")
        return ":".join(parts)

    def get(self, tool: str, **params) -> Optional[any]:
        if not self.enabled:
            return None

        with self._lock:
            key = self._make_key(tool, **params)
            entry = self._entries.get(key)

            if entry and not entry.is_expired:
                entry.hit_count += 1
                self._entries.move_to_end(key)
                self._stats["hits"] += 1
                return entry.data

            self._stats["misses"] += 1
            return None

    def set(self, data: any, tool: str, ttl: int = None, **params) -> str:
        if not self.enabled:
            return ""

        with self._lock:
            key = self._make_key(tool, **params)
            now = time.time()

            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)

            import json
            try:
                size = len(json.dumps(data, default=str))
            except Exception:
                size = 0

            entry = MetadataEntry(
                key=key,
                data=data,
                created_at=now,
                expires_at=now + (ttl or self.ttl),
                data_size_bytes=size,
            )
            self._entries[key] = entry
            return key

    def invalidate_table(self, table_name: str):
        """失效某张表的相关缓存"""
        with self._lock:
            keys = [k for k in self._entries if table_name.lower() in k.lower()]
            for k in keys:
                self._entries.pop(k, None)

    def invalidate_all(self):
        with self._lock:
            self._entries.clear()

    def clear(self):
        self.invalidate_all()
        self._stats = {"hits": 0, "misses": 0}

    def get_stats(self) -> dict:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            oldest = min((e.created_at for e in self._entries.values()), default=0)
            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "hit_rate": round(self._stats["hits"] / total, 4) if total else 0,
                "entries": len(self._entries),
                "oldest_entry_age_sec": round(time.time() - oldest, 1) if oldest else 0,
            }


GLOBAL_METADATA_CACHE = MetadataCache()