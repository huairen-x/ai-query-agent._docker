"""
缓存模块 - 语义缓存 + 元数据缓存 + 结果缓存（SQLite 持久化）
"""
from cache.sqlite_cache import (
    SQLiteCache,
    SemanticCache as SemanticSQLiteCache, GLOBAL_SEMANTIC_CACHE,
    MetadataCache as MetadataSQLiteCache, GLOBAL_METADATA_CACHE,
    ResultCache as ResultSQLiteCache, GLOBAL_RESULT_CACHE,
)

__all__ = [
    "SQLiteCache",
    "SemanticSQLiteCache", "GLOBAL_SEMANTIC_CACHE",
    "MetadataSQLiteCache", "GLOBAL_METADATA_CACHE",
    "ResultSQLiteCache", "GLOBAL_RESULT_CACHE",
]