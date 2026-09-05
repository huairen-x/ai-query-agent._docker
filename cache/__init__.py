"""
缓存模块 - 语义缓存 + 元数据缓存 + 结果缓存（SQLite 持久化）
"""
from cache.semantic_cache import SemanticCache, GLOBAL_SEMANTIC_CACHE
from cache.metadata_cache import MetadataCache, GLOBAL_METADATA_CACHE
from cache.result_cache import ResultCache, GLOBAL_RESULT_CACHE
from cache.sqlite_cache import SQLiteCache, SemanticCache as SemanticSQLiteCache, MetadataCache as MetadataSQLiteCache, ResultCache as ResultSQLiteCache

__all__ = [
    "SemanticCache", "GLOBAL_SEMANTIC_CACHE",
    "MetadataCache", "GLOBAL_METADATA_CACHE",
    "ResultCache", "GLOBAL_RESULT_CACHE",
    "SQLiteCache", "SemanticSQLiteCache", "MetadataSQLiteCache", "ResultSQLiteCache",
]