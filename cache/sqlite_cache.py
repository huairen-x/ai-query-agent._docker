"""
统一 SQLite 缓存引擎（元数据 / 结果 / 语义三级缓存）

列名契约：本模块写入的列必须与 db/schema.py 中的建表语句一致。
历史上两侧列名不一致（data/data_size vs result/sql_text/row_count），
且异常被 `except: pass` 吞掉，导致缓存静默永不命中。现在失败会打日志。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from db.manager import GLOBAL_DB_MANAGER

logger = logging.getLogger("cache")


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    entries: int = 0
    hit_rate: float = 0.0


class SQLiteCache:
    """
    基于 SQLite 的通用缓存引擎

    子类负责声明：
      - table_name：表名
      - _columns：写入时使用的列
      - _build_row(key, data, now, expires)：返回待写入的行
      - _read_row(row)：把数据库行还原成业务对象
    """

    table_name: str = ""
    _columns: tuple[str, ...] = ()

    def __init__(self, table_name: str, ttl: int = 300, max_entries: int = 10000):
        self.table_name = table_name
        self.ttl = ttl
        self.max_entries = max_entries
        self._enabled = True
        self._stats = {"hits": 0, "misses": 0, "evictions": 0}

    # ── 子类钩子 ──────────────────────────────────────────
    def _build_row(self, key: str, data: Any, now: float, expires: float) -> dict:
        raise NotImplementedError

    def _read_row(self, row: dict) -> Any:
        raise NotImplementedError

    # ── 通用读写 ──────────────────────────────────────────
    def get(self, key: str) -> Optional[Any]:
        """获取缓存，未命中/过期返回 None"""
        if not self._enabled:
            return None

        try:
            row = GLOBAL_DB_MANAGER.fetch_one(
                f"SELECT * FROM {self.table_name} WHERE cache_key = ? AND expires_at > ?",
                (key, time.time()),
            )
            if row is None:
                self._stats["misses"] += 1
                return None

            GLOBAL_DB_MANAGER.execute(
                f"UPDATE {self.table_name} SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (key,),
            )
            self._stats["hits"] += 1
            return self._read_row(row)
        except Exception as exc:
            self._stats["misses"] += 1
            logger.warning("cache get failed table=%s key=%s: %s", self.table_name, key, exc)
            return None

    def set(self, key: str, data: Any, ttl: int = None) -> None:
        """写入缓存，并淘汰超出 max_entries 的最旧条目"""
        if not self._enabled:
            return

        now = time.time()
        expires = now + (ttl or self.ttl)
        try:
            self._evict()
            GLOBAL_DB_MANAGER.insert(self.table_name, self._build_row(key, data, now, expires))
        except Exception as exc:
            logger.warning("cache set failed table=%s key=%s: %s", self.table_name, key, exc)

    def delete(self, key: str) -> None:
        """删除缓存"""
        try:
            GLOBAL_DB_MANAGER.execute(
                f"DELETE FROM {self.table_name} WHERE cache_key = ?", (key,)
            )
        except Exception as exc:
            logger.warning("cache delete failed table=%s key=%s: %s", self.table_name, key, exc)

    def clear(self) -> None:
        """清空缓存"""
        try:
            GLOBAL_DB_MANAGER.execute(f"DELETE FROM {self.table_name}")
        except Exception as exc:
            logger.warning("cache clear failed table=%s: %s", self.table_name, exc)

    def _evict(self) -> None:
        """保留最新的 max_entries 条，其余按 created_at 从旧到新删除"""
        deleted = GLOBAL_DB_MANAGER.execute(
            f"DELETE FROM {self.table_name} WHERE cache_key IN ("
            f"SELECT cache_key FROM {self.table_name} ORDER BY created_at DESC "
            f"LIMIT -1 OFFSET ?)",
            (self.max_entries,),
        )
        if deleted.rowcount > 0:
            self._stats["evictions"] += deleted.rowcount

    def get_stats(self) -> CacheStats:
        """获取缓存统计"""
        total = self._stats["hits"] + self._stats["misses"]
        try:
            entries = GLOBAL_DB_MANAGER.count(self.table_name)
        except Exception as exc:
            logger.warning("cache count failed table=%s: %s", self.table_name, exc)
            entries = 0

        return CacheStats(
            hits=self._stats["hits"],
            misses=self._stats["misses"],
            entries=entries,
            hit_rate=round(self._stats["hits"] / total, 4) if total else 0.0,
        )


class MetadataCache(SQLiteCache):
    """元数据缓存：键为 tool + 参数，值任意 JSON 结构"""

    def __init__(self, ttl: int = 300):
        super().__init__("metadata_cache", ttl=ttl)

    def get(self, tool: str, **params) -> Optional[Any]:
        return super().get(self._make_key(tool, **params))

    def set(self, data: Any, tool: str, ttl: int = None, **params) -> None:
        super().set(self._make_key(tool, **params), data, ttl=ttl)

    def _make_key(self, tool: str, **params) -> str:
        parts = [tool]
        for key, value in sorted(params.items()):
            parts.append(f"{key}={value}")
        return ":".join(parts)

    def _build_row(self, key: str, data: Any, now: float, expires: float) -> dict:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        return {
            "cache_key": key,
            "data": payload,
            "data_size": len(payload),
            "hit_count": 0,
            "created_at": now,
            "expires_at": expires,
        }

    def _read_row(self, row: dict) -> Any:
        return json.loads(row["data"])

    def invalidate_table(self, table_name: str) -> int:
        """失效某张表的相关缓存"""
        try:
            rows = GLOBAL_DB_MANAGER.fetch_all(
                "SELECT cache_key FROM metadata_cache WHERE cache_key LIKE ?",
                (f"%table_name={table_name}%",),
            )
        except Exception as exc:
            logger.warning("invalidate_table failed table=%s: %s", table_name, exc)
            return 0
        for row in rows:
            self.delete(row["cache_key"])
        return len(rows)


class ResultCache(SQLiteCache):
    """
    SQL 查询结果缓存

    值契约：set(sql, {"rows": [...], "columns": [...]}, row_count=n)
            get(sql) -> {"rows": [...], "columns": [...], "row_count": n}
    """

    def __init__(self, ttl: int = 60):
        super().__init__("result_cache", ttl=ttl)

    def get(self, sql: str) -> Optional[dict]:
        payload = super().get(self._make_hash(sql))
        if payload is None:
            return None
        return payload

    def set(self, sql: str, payload: dict, row_count: int = 0, ttl: int = None) -> None:
        super().set(
            self._make_hash(sql),
            {"payload": payload, "row_count": row_count},
            ttl=ttl,
        )

    def _normalize_sql(self, sql: str) -> str:
        import re
        return re.sub(r"\s+", " ", sql.strip()).lower()

    def _make_hash(self, sql: str) -> str:
        return hashlib.md5(self._normalize_sql(sql).encode()).hexdigest()

    def _build_row(self, key: str, data: Any, now: float, expires: float) -> dict:
        payload = data["payload"]
        return {
            "cache_key": key,
            "sql_text": json.dumps(payload, ensure_ascii=False, default=str)[:2000],
            "result": json.dumps(payload, ensure_ascii=False, default=str),
            "row_count": data.get("row_count", 0),
            "hit_count": 0,
            "created_at": now,
            "expires_at": expires,
        }

    def _read_row(self, row: dict) -> dict:
        payload = json.loads(row["result"])
        if not isinstance(payload, dict):
            payload = {"rows": payload}
        payload.setdefault("columns", [])
        payload["row_count"] = row["row_count"]
        return payload


class SemanticCache(SQLiteCache):
    """
    语义缓存（SQLite 持久化）

    当前 ask_question 工作流尚未接入语义缓存，本类保留给后续按问题复用的场景。
    """

    def __init__(self, ttl: int = 300, threshold: float = 0.85):
        super().__init__("semantic_cache", ttl=ttl)
        self.threshold = threshold

    def get(self, question: str) -> Optional[dict]:
        return super().get(self._make_hash(question))

    def set(self, question: str, result: Any, sql: str = "",
            business_tags: list = None, ttl: int = None) -> None:
        super().set(self._make_hash(question), {
            "result": result,
            "sql": sql,
            "business_tags": business_tags or [],
            "question": question,
        }, ttl=ttl)

    def _make_hash(self, question: str) -> str:
        return hashlib.md5(question.strip().lower().encode()).hexdigest()

    def _build_row(self, key: str, data: Any, now: float, expires: float) -> dict:
        return {
            "cache_key": key,
            "question": data.get("question", ""),
            "result": json.dumps(data.get("result"), ensure_ascii=False, default=str),
            "sql_text": data.get("sql", ""),
            "embedding": None,
            "business_tags": json.dumps(data.get("business_tags", []), ensure_ascii=False),
            "hit_count": 0,
            "created_at": now,
            "expires_at": expires,
        }

    def _read_row(self, row: dict) -> dict:
        return {
            "result": json.loads(row["result"]),
            "sql": row["sql_text"],
            "business_tags": json.loads(row["business_tags"] or "[]"),
            "question": row["question"],
        }


# 全局单例
GLOBAL_SEMANTIC_CACHE = SemanticCache()
GLOBAL_METADATA_CACHE = MetadataCache()
GLOBAL_RESULT_CACHE = ResultCache()
