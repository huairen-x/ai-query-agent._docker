"""Unit tests for ContextCleaner"""
from __future__ import annotations
import pytest
from compressor.cleanup import GLOBAL_CONTEXT_CLEANER, ContextCleaner


class TestContextCleaner:
    """ContextCleaner 基础功能"""

    def test_cleanup_sql_removes_comment_lines(self):
        sql = "SELECT * FROM users\n-- this is a comment\nWHERE id = 1"
        cleaned, result = GLOBAL_CONTEXT_CLEANER.cleanup_sql(sql)
        assert "--" not in cleaned
        assert "SELECT" in cleaned

    def test_cleanup_sql_removes_multiline_comments(self):
        sql = "SELECT * /* block comment */ FROM users"
        cleaned, result = GLOBAL_CONTEXT_CLEANER.cleanup_sql(sql)
        assert "/*" not in cleaned

    def test_cleanup_sql_normalizes_whitespace(self):
        sql = "SELECT   *    FROM    users"
        cleaned, result = GLOBAL_CONTEXT_CLEANER.cleanup_sql(sql)
        assert "  " not in cleaned

    def test_cleanup_sql_returns_stats(self):
        sql = "SELECT * FROM users"
        cleaned, result = GLOBAL_CONTEXT_CLEANER.cleanup_sql(sql)
        # result is a CleanupResult dataclass, not a dict
        assert hasattr(result, "original_size")
        assert hasattr(result, "cleaned_size")
        assert result.original_size > 0

    def test_cleanup_empty_string(self):
        cleaned, result = GLOBAL_CONTEXT_CLEANER.cleanup_sql("")
        assert cleaned == ""

    def test_cleanup_sql_preserves_valid_sql(self):
        sql = "SELECT id, name FROM users WHERE status = 'active' ORDER BY id"
        cleaned, result = GLOBAL_CONTEXT_CLEANER.cleanup_sql(sql)
        assert "SELECT" in cleaned
        assert "WHERE" in cleaned

    def test_context_cleaner_global_singleton(self):
        assert GLOBAL_CONTEXT_CLEANER is not None
        assert isinstance(GLOBAL_CONTEXT_CLEANER, ContextCleaner)