"""
语义缓存 - 基于 embedding 相似度的查询缓存
将自然语言问题映射到缓存空间，相似问题直接返回缓存结果
"""
from __future__ import annotations
import time
import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Optional
from collections import OrderedDict

from engine.config import GLOBAL_CONFIG


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    question: str
    result: any
    sql: str = ""
    business_tags: list = field(default_factory=list)
    created_at: float = 0.0
    expires_at: float = 0.0
    hit_count: int = 0
    embedding: list[float] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class SemanticCache:
    """
    语义缓存
    支持：
    - 精确匹配（hash）
    - 语义相似匹配（embedding 余弦相似度）
    - 业务标签匹配
    - TTL 过期
    - LRU 淘汰
    """

    def __init__(self):
        config = GLOBAL_CONFIG.cache
        self.enabled = config.semantic_cache_enabled
        self.ttl = config.semantic_cache_ttl
        self.threshold = config.semantic_cache_threshold
        self.business_threshold = config.semantic_cache_business_threshold
        self.max_entries = 10000

        self._entries: dict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = {"hits": 0, "misses": 0, "semantic_hits": 0}

    def _make_key(self, question: str) -> str:
        return hashlib.md5(question.strip().lower().encode()).hexdigest()

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """计算余弦相似度"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _get_tags(self, question: str) -> list[str]:
        """从问题中提取业务标签"""
        import re
        tags = []
        patterns = [
            r'(?:近|过去|最近)\s*(\d+)\s*(天|周|月|年)',
            r'(?:按|根据|按照)\s*(\w+)',
            r'(?:的|在|于)\s*(\w+)\s*(?:报告|分析|统计)',
        ]
        for p in patterns:
            matches = re.findall(p, question)
            for m in matches:
                if isinstance(m, tuple):
                    tags.extend(m)
                else:
                    tags.append(m)
        return tags

    def get(self, question: str, embedding: list[float] = None,
            business_tags: list[str] = None) -> Optional[any]:
        """获取缓存"""
        if not self.enabled:
            return None

        with self._lock:
            exact_key = self._make_key(question)
            entry = self._entries.get(exact_key)

            # 1. 精确匹配
            if entry and not entry.is_expired:
                entry.hit_count += 1
                self._entries.move_to_end(exact_key)
                self._stats["hits"] += 1
                return entry.result

            # 2. 语义相似匹配
            if embedding:
                best_score = 0.0
                best_entry = None
                for ekey, eentry in self._entries.items():
                    if eentry.is_expired:
                        continue
                    if not eentry.embedding:
                        continue
                    score = self._cosine_similarity(embedding, eentry.embedding)

                    # 业务标签加权
                    if business_tags and eentry.business_tags:
                        tag_overlap = len(set(business_tags) & set(eentry.business_tags))
                        if tag_overlap > 0:
                            score += 0.05 * tag_overlap

                    if score > best_score:
                        best_score = score
                        best_entry = eentry

                threshold = self.business_threshold if business_tags else self.threshold
                if best_score >= threshold and best_entry:
                    best_entry.hit_count += 1
                    self._stats["semantic_hits"] += 1
                    self._stats["hits"] += 1
                    return best_entry.result

            self._stats["misses"] += 1
            return None

    def set(self, question: str, result: any, sql: str = "",
            embedding: list[float] = None, ttl: int = None) -> str:
        """写入缓存"""
        if not self.enabled:
            return ""

        with self._lock:
            key = self._make_key(question)
            now = time.time()

            # LRU 淘汰
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)

            entry = CacheEntry(
                key=key,
                question=question,
                result=result,
                sql=sql,
                business_tags=self._get_tags(question),
                created_at=now,
                expires_at=now + (ttl or self.ttl),
                embedding=embedding or [],
            )
            self._entries[key] = entry
            return key

    def invalidate(self, question: str = None, pattern: str = None):
        """失效缓存"""
        with self._lock:
            if question:
                key = self._make_key(question)
                self._entries.pop(key, None)
            elif pattern:
                import re
                keys = [k for k in self._entries if re.search(pattern, k)]
                for k in keys:
                    self._entries.pop(k, None)

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._stats = {"hits": 0, "misses": 0, "semantic_hits": 0}

    def get_stats(self) -> dict:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "semantic_hits": self._stats["semantic_hits"],
                "hit_rate": round(self._stats["hits"] / total, 4) if total else 0,
                "entries": len(self._entries),
            }


GLOBAL_SEMANTIC_CACHE = SemanticCache()