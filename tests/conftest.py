"""Shared fixtures for all tests"""
from __future__ import annotations
import pytest

from engine.config import GLOBAL_CONFIG, AppConfig, ContextCleanupConfig
from engine.token_budget import GLOBAL_BUDGET_CONTROLLER, TokenBudgetController
from compressor.pipeline import GLOBAL_CONTEXT_PIPELINE, ContextPipeline
from compressor.engine import GLOBAL_HEADROOM


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global singletons before each test"""
    GLOBAL_BUDGET_CONTROLLER._snapshots.clear()
    GLOBAL_CONTEXT_PIPELINE._stats = {"processed": 0, "l1_only": 0, "l2_only": 0, "l3_used": 0}
    GLOBAL_HEADROOM.reset_stats()
    yield


@pytest.fixture
def small_state():
    """State below threshold A — should hit the fast path"""
    return {
        "question": "how many users?",
        "sql": "SELECT count(*) FROM users",
        "tenant_id": "default",
    }


@pytest.fixture
def medium_state():
    """State that triggers L1 but not L2"""
    return {
        "question": "what is the revenue trend?",
        "sql": "SELECT date, sum(amount) FROM orders GROUP BY date",
        "errors": [f"warn_{i}" for i in range(15)],
        "audit_trail": [f"entry_{i}" for i in range(25)],
        "cache_hits": 42,
        "result_cache_hit": True,
        "metadata_cache_hit": False,
        "tenant_id": "default",
        "interpretation": "short text",
        "cleanup_result": {"original_length": 100, "cleaned_length": 80, "ratio": 0.8},
    }


@pytest.fixture
def large_state():
    """State that exceeds both thresholds — L1 + L2 required"""
    return {
        "question": "show me the quarterly revenue breakdown by region " * 100,
        "sql": "SELECT region, quarter, sum(revenue) FROM sales GROUP BY region, quarter",
        "interpretation": "The revenue data shows " * 500,
        "intent": "analyze revenue trends across regions " * 50,
        "chart_suggestion": "bar chart of revenue by region " * 30,
        "errors": [f"error_{i}" for i in range(20)],
        "query_result": {
            "rows": [{"id": i, "value": f"row_data_{i}"} for i in range(100)],
            "row_count": 100,
        },
        "metadata": {"tables": [f"table_{i}" for i in range(20)]},
        "audit_trail": [f"entry_{i}" for i in range(30)],
        "cache_hits": 42,
        "result_cache_hit": True,
        "metadata_cache_hit": False,
        "stack_trace": "Traceback (most recent call last):\n  File \"app.py\", line 42, in query\n  File \"db.py\", line 15, in execute\n  File \"db.py\", line 20, in _connect\nException: connection refused\n" * 20,
        "tenant_id": "default",
    }


@pytest.fixture
def budget():
    """Create a fresh budget for pipeline testing"""
    return GLOBAL_BUDGET_CONTROLLER.create_workflow_budget("test-wf", "deepseek-flash")


@pytest.fixture
def pipeline():
    """Pipeline singleton"""
    return GLOBAL_CONTEXT_PIPELINE