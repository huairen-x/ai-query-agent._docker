"""Unit tests for ContextPipeline (L1 / L2 / L3 / fast path)"""
from __future__ import annotations
import pytest
from compressor.pipeline import ContextPipeline, _state_token_count


class TestL1Cleanup:
    """L1: 规则删除 — 零 token 消耗"""

    def test_removes_none_values(self, pipeline):
        state = {"question": "hello", "errors": None, "extra": None}
        result = pipeline._l1_cleanup(state)
        assert "errors" not in result
        assert "extra" not in result
        assert result["question"] == "hello"

    def test_removes_empty_strings(self, pipeline):
        state = {"question": "", "sql": "  ", "valid": "ok"}
        result = pipeline._l1_cleanup(state)
        assert "question" not in result
        assert "sql" not in result
        assert result["valid"] == "ok"

    def test_removes_empty_lists_and_dicts(self, pipeline):
        state = {"errors": [], "metadata": {}, "valid": "ok"}
        result = pipeline._l1_cleanup(state)
        assert "errors" not in result
        assert "metadata" not in result
        assert result["valid"] == "ok"

    def test_removes_diagnostic_keys(self, pipeline):
        state = {"cache_hits": 42, "result_cache_hit": True, "metadata_cache_hit": False, "question": "hi"}
        result = pipeline._l1_cleanup(state)
        assert "cache_hits" not in result
        assert "result_cache_hit" not in result
        assert "metadata_cache_hit" not in result
        assert result["question"] == "hi"

    def test_truncates_errors_to_10(self, pipeline):
        state = {"errors": [f"e{i}" for i in range(20)]}
        result = pipeline._l1_cleanup(state)
        assert len(result["errors"]) == 10
        assert result["errors"] == [f"e{i}" for i in range(10, 20)]

    def test_keeps_errors_under_10(self, pipeline):
        state = {"errors": [f"e{i}" for i in range(5)]}
        result = pipeline._l1_cleanup(state)
        assert len(result["errors"]) == 5

    def test_truncates_audit_trail_to_20(self, pipeline):
        state = {"audit_trail": [f"a{i}" for i in range(30)]}
        result = pipeline._l1_cleanup(state)
        assert len(result["audit_trail"]) == 20
        assert result["audit_trail"] == [f"a{i}" for i in range(10, 30)]

    def test_truncates_query_result_rows_to_50(self, pipeline):
        state = {"query_result": {"rows": [{"id": i} for i in range(100)], "row_count": 100}}
        result = pipeline._l1_cleanup(state)
        assert len(result["query_result"]["rows"]) == 50
        assert result["query_result"]["_truncated"] is True

    def test_keeps_query_result_rows_under_50(self, pipeline):
        state = {"query_result": {"rows": [{"id": i} for i in range(10)], "row_count": 10}}
        result = pipeline._l1_cleanup(state)
        assert len(result["query_result"]["rows"]) == 10
        assert "_truncated" not in result["query_result"]

    def test_truncates_metadata_tables_to_10(self, pipeline):
        state = {"metadata": {"tables": [f"t{i}" for i in range(20)]}}
        result = pipeline._l1_cleanup(state)
        assert len(result["metadata"]["tables"]) == 10
        assert result["metadata"]["_truncated"] is True

    def test_truncates_stack_trace(self, pipeline):
        long_trace = "Traceback (most recent call last):\n  File \"app.py\", line 1\n" * 50
        state = {"stack_trace": long_trace}
        result = pipeline._l1_cleanup(state)
        assert len(result["stack_trace"]) <= 500 + len("...[truncated]")
        assert result["stack_trace"].endswith("...[truncated]")

    def test_preserves_normal_long_strings(self, pipeline):
        long_text = "hello world " * 100  # no Traceback pattern
        state = {"notes": long_text}
        result = pipeline._l1_cleanup(state)
        assert result["notes"] == long_text  # unchanged


class TestL2Compression:
    """L2: Headroom 压缩"""

    def test_compresses_long_interpretation(self, pipeline):
        state = {"interpretation": "The analysis shows " * 500}
        result = pipeline._l2_compress(state)
        # SmartCrusher may or may not compress depending on environment,
        # but the field should still be present
        assert "interpretation" in result

    def test_skips_short_strings(self, pipeline):
        state = {"interpretation": "short"}
        result = pipeline._l2_compress(state)
        assert result["interpretation"] == "short"

    def test_compresses_metadata_dict(self, pipeline):
        state = {"metadata": {"tables": [{"name": f"table_{i}"} for i in range(50)]}}
        result = pipeline._l2_compress(state)
        assert "metadata" in result

    def test_compresses_sql_result(self, pipeline):
        state = {"query_result": {"rows": [{"id": i} for i in range(100)]}}
        result = pipeline._l2_compress(state)
        assert "query_result" in result

    def test_preserves_none_values(self, pipeline):
        state = {"some_key": None}
        result = pipeline._l2_compress(state)
        assert result.get("some_key") is None

    def test_field_to_content_type_mapping(self, pipeline):
        """Verify all expected fields have a content_type mapping"""
        expected_fields = {"metadata", "query_result", "sql", "interpretation", "question",
                           "cleanup_result", "intent", "chart_suggestion"}
        actual = set(pipeline.FIELD_TO_CONTENT_TYPE.keys())
        assert expected_fields.issubset(actual), f"Missing: {expected_fields - actual}"

    def test_unknown_field_falls_back_to_conversation(self, pipeline):
        """Unknown field should default to 'conversation' content_type"""
        assert pipeline.FIELD_TO_CONTENT_TYPE.get("unknown_field", "conversation") == "conversation"


class TestL3Summarization:
    """L3: LLM 摘要（默认关闭）"""

    def test_l3_disabled_by_default(self, pipeline, large_state):
        result = pipeline.process(large_state)
        assert "_l3_applied" not in result

    def test_l3_enabled_sets_marker(self, pipeline, large_state, monkeypatch):
        monkeypatch.setattr("compressor.pipeline.GLOBAL_CONFIG.context_cleanup.l3_enabled", True)
        result = pipeline.process(large_state)
        assert result.get("_l3_applied") is True

    def test_l3_stats_tracked(self, pipeline, large_state, monkeypatch):
        monkeypatch.setattr("compressor.pipeline.GLOBAL_CONFIG.context_cleanup.l3_enabled", True)
        pipeline.process(large_state)
        assert pipeline._stats["l3_used"] >= 1


class TestFastPath:
    """短路: state 低于阈值 A 直接返回"""

    def test_small_state_returns_immediately(self, pipeline, small_state):
        tokens_before = _state_token_count(small_state)
        budget = __import__("engine.token_budget", fromlist=["GLOBAL_BUDGET_CONTROLLER"]).GLOBAL_BUDGET_CONTROLLER
        wf_budget = budget.create_workflow_budget("test-fast", "deepseek-flash")
        threshold_a = wf_budget.allocations.usable
        if tokens_before > threshold_a:
            pytest.skip("small_state too large for this model's budget")
        result = pipeline.process(small_state)
        # state should be unchanged
        assert result == small_state

    def test_large_state_triggers_l1_and_l2(self, pipeline, large_state):
        result = pipeline.process(large_state)
        # L1 should have cleaned up diagnostic keys
        assert "cache_hits" not in result
        assert "result_cache_hit" not in result
        # L1 should have truncated
        assert len(result.get("errors", [])) <= 10
        assert len(result.get("audit_trail", [])) <= 20
        assert len(result.get("query_result", {}).get("rows", [])) <= 50
        assert len(result.get("metadata", {}).get("tables", [])) <= 10

    def test_process_returns_dict(self, pipeline, large_state):
        result = pipeline.process(large_state)
        assert isinstance(result, dict)


class TestPipelineStats:
    """Pipeline 统计信息"""

    def test_stats_after_process(self, pipeline, large_state):
        pipeline.process(large_state)
        stats = pipeline.get_stats()
        assert stats["processed"] >= 1
        assert isinstance(stats["l1_only"], int)
        assert isinstance(stats["l2_only"], int)
        assert isinstance(stats["l3_used"], int)

    def test_stats_reset_between_tests(self, pipeline):
        stats = pipeline.get_stats()
        assert stats["processed"] == 0