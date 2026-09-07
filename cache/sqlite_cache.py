"""
统一 SQLite 缓存引擎
替换原有的内存 dict 缓存，提供持久化能力
"""
from __future__ import annotations
import json
import time
import hashlib
import threading
from typing import Optional, Any
from dataclasses import dataclass

from db.manager import GLOBAL_DB_MANAGER
from observability import cache_hits_total, cache_misses_total, cache_size
from resilience import fallback


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    entries: int = 0
    hit_rate: float = 0.0


class SQLiteCache:
    """
    基于 SQLite 的通用缓存引擎
    支持：语义缓存 / 元数据缓存 / 结果缓存
    """

    def __init__(self, table_name: str, ttl: int = 300, max_entries: int = 10000):
        self.table_name = table_name
        self.ttl = ttl
        self.max_entries = max_entries
        self._enabled = True
        self._stats = {"hits": 0, "misses": 0, "evictions": 0}

    @fallback(result=None)
    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        if not self._enabled:
            return None

        try:
            row = GLOBAL_DB_MANAGER.fetch_one(
                f"SELECT data, hit_count FROM {self.table_name} WHERE cache_key = ? AND expires_at > ?",
                (key, time.time())
            )
            if row:
                # 更新命中次数
                GLOBAL_DB_MANAGER.execute(
                    f"UPDATE {self.table_name} SET hit_count = hit_count + 1 WHERE cache_key = ?",
                    (key,)
                )
                self._stats["hits"] += 1
                cache_hits_total.inc(cache_type=self.table_name)
                return json.loads(row["data"])
            self._stats["misses"] += 1
            cache_misses_total.inc(cache_type=self.table_name)
            return None
        except Exception:
            self._stats["misses"] += 1
            cache_misses_total.inc(cache_type=self.table_name)
            return None

    def set(self, key: str, data: Any, ttl: int = None):
        """写入缓存"""
        if not self._enabled:
            return

        now = time.time()
        expires = now + (ttl or self.ttl)

        try:
            # LRU 淘汰：删除最旧的条目
            GLOBAL_DB_MANAGER.execute(
                f"DELETE FROM {self.table_name} WHERE cache_key IN ("
                f"SELECT cache_key FROM {self.table_name} ORDER BY created_at ASC "
                f"LIMIT CASE WHEN (SELECT COUNT(*) FROM {self.table_name}) >= ? "
                f"THEN (SELECT COUNT(*) - ? + 1 FROM {self.table_name}) ELSE 0 END"
                f")",
                (self.max_entries, self.max_entries)
            )

            GLOBAL_DB_MANAGER.insert(self.table_name, {
                "cache_key": key,
                "data": json.dumps(data, ensure_ascii=False, default=str),
                "data_size": len(json.dumps(data, ensure_ascii=False, default=str)),
                "hit_count": 0,
                "created_at": now,
                "expires_at": expires,
            })
            # 更新缓存大小指标
            try:
                row = GLOBAL_DB_MANAGER.fetch_one(
                    f"SELECT COUNT(*) as cnt FROM {self.table_name}"
                )
                if row:
                    cache_size.set(row["cnt"], cache_type=self.table_name)
            except Exception:
                pass
        except Exception:
            pass

    def delete(self, key: str):
        """删除缓存"""
        try:
            GLOBAL_DB_MANAGER.execute(
                f"DELETE FROM {self.table_name} WHERE cache_key = ?", (key,)
            )
        except Exception:
            pass

    def clear(self):
        """清空缓存"""
        try:
            GLOBAL_DB_MANAGER.execute(f"DELETE FROM {self.table_name}")
            cache_size.set(0, cache_type=self.table_name)
        except Exception:
            pass

    def get_stats(self) -> CacheStats:
        """获取缓存统计"""
        total = self._stats["hits"] + self._stats["misses"]
        try:
            row = GLOBAL_DB_MANAGER.fetch_one(
                f"SELECT COUNT(*) as cnt FROM {self.table_name}"
            )
            entries = row["cnt"] if row else 0
        except Exception:
            entries = 0

        return CacheStats(
            hits=self._stats["hits"],
            misses=self._stats["misses"],
            entries=entries,
            hit_rate=round(self._stats["hits"] / total, 4) if total else 0,
        )


class SemanticCache(SQLiteCache):
    """
    语义缓存（基于 SQLite）
    支持精确匹配 + 业务标签匹配
    """

    def __init__(self, ttl: int = 300, threshold: float = 0.85):
        super().__init__("semantic_cache", ttl=ttl)
        self.threshold = threshold
        self._stats["semantic_hits"] = 0

    def _make_key(self, question: str) -> str:
        return hashlib.md5(question.strip().lower().encode()).hexdigest()

    def get(self, question: str) -> Optional[Any]:
        """获取语义缓存（精确匹配）"""
        key = self._make_key(question)
        return super().get(key)

    def set(self, question: str, result: Any, sql: str = "", business_tags: list = None, ttl: int = None):
        """写入语义缓存"""
        key = self._make_key(question)
        # 存储额外信息到扩展字段
        data = {
            "result": result,
            "sql": sql,
            "business_tags": business_tags or [],
            "question": question,
        }
        super().set(key, data, ttl=ttl)


class MetadataCache(SQLiteCache):
    """元数据缓存"""

    def __init__(self, ttl: int = 300):
        super().__init__("metadata_cache", ttl=ttl)

    def get(self, tool: str, **params) -> Optional[Any]:
        key = self._make_key(tool, **params)
        return super().get(key)

    def set(self, data: Any, tool: str, ttl: int = None, **params):
        key = self._make_key(tool, **params)
        super().set(key, data, ttl=ttl)

    def _make_key(self, tool: str, **params) -> str:
        parts = [tool]
        for k, v in sorted(params.items()):
            parts.append(f"{k}={v}")
        return ":".join(parts)

    def invalidate_table(self, table_name: str):
        """失效某张表的相关缓存"""
        try:
            rows = GLOBAL_DB_MANAGER.fetch_all(
                "SELECT cache_key FROM metadata_cache WHERE cache_key LIKE ?",
                (f"%table_name={table_name}%",)
            )
            for row in rows:
                self.delete(row["cache_key"])
        except Exception:
            pass


class ResultCache(SQLiteCache):
    """SQL 查询结果缓存"""

    def __init__(self, ttl: int = 60):
        super().__init__("result_cache", ttl=ttl)

    def _normalize_sql(self, sql: str) -> str:
        import re
        sql = re.sub(r'\s+', ' ', sql.strip())
        return sql.lower()

    def _make_hash(self, sql: str) -> str:
        return hashlib.md5(self._normalize_sql(sql).encode()).hexdigest()

    def get(self, sql: str) -> Optional[Any]:
        h = self._make_hash(sql)
        return super().get(h)

    def set(self, sql: str, result: Any, row_count: int = 0, ttl: int = None):
        h = self._make_hash(sql)
        data = {"result": result, "row_count": row_count, "sql": sql}
        super().set(h, data, ttl=ttl)


# 全局单例
GLOBAL_SEMANTIC_CACHE = SemanticCache()
GLOBAL_METADATA_CACHE = MetadataCache()
GLOBAL_RESULT_CACHE = ResultCache()