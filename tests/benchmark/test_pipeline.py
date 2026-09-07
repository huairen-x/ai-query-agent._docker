"""
Benchmark tests for ContextPipeline
每次优化前后执行：pytest tests/benchmark/ --benchmark-only --benchmark-json=result.json
对比 tokens_before / tokens_after / layers_applied / elapsed 变化
"""
from __future__ import annotations
import pytest
from compressor.pipeline import GLOBAL_CONTEXT_PIPELINE, _state_token_count


def _make_large_state(size: int = 5000) -> dict:
    """Generate a state with approximately `size` tokens"""
    repeat = max(1, size // 10)
    return {
        "question": "what is the quarterly revenue trend? " * repeat,
        "sql": "SELECT date, region, sum(amount) FROM orders GROUP BY date, region",
        "interpretation": "The analysis of revenue data shows " * repeat,
        "intent": "analyze revenue growth across regions " * (repeat // 2),
        "chart_suggestion": "bar chart of revenue by quarter " * (repeat // 3),
        "errors": [f"error_{i}" for i in range(20)],
        "query_result": {
            "rows": [{"id": i, "value": f"row_data_{i}_{'x' * 50}"} for i in range(100)],
            "row_count": 100,
        },
        "metadata": {"tables": [{"name": f"table_{i}", "columns": 10} for i in range(20)]},
        "audit_trail": [{"step": i, "action": "query", "status": "ok"} for i in range(30)],
        "cache_hits": 42,
        "result_cache_hit": True,
        "tenant_id": "default",
    }


@pytest.mark.benchmark(group="pipeline", min_rounds=20, warmup=True)
def test_pipeline_throughput(benchmark):
    """Benchmark: throughput of pipeline.process() with large state"""
    size = 5000
    state = _make_large_state(size)

    def run():
        result = GLOBAL_CONTEXT_PIPELINE.process(state, workflow_id="benchmark", model="deepseek-flash")
        return result

    result = benchmark(run)
    tokens_before = _state_token_count(state)
    tokens_after = _state_token_count(result)
    print(f"\n  tokens_before={tokens_before}, tokens_after={tokens_after}, "
          f"ratio={tokens_after/tokens_before:.2f}")


@pytest.mark.benchmark(group="pipeline", min_rounds=50, warmup=True)
def test_pipeline_small_state(benchmark):
    """Benchmark: fast path for small state (no processing needed)"""
    state = {"question": "hi", "sql": "SELECT 1", "tenant_id": "default"}

    def run():
        return GLOBAL_CONTEXT_PIPELINE.process(state, workflow_id="fast", model="deepseek-flash")

    benchmark(run)


@pytest.mark.benchmark(group="pipeline_l1", min_rounds=50, warmup=True)
def test_l1_cleanup_throughput(benchmark):
    """Benchmark: L1 cleanup only"""
    state = _make_large_state(5000)

    def run():
        return GLOBAL_CONTEXT_PIPELINE._l1_cleanup(state)

    benchmark(run)


@pytest.mark.benchmark(group="pipeline_l2", min_rounds=20, warmup=True)
def test_l2_compress_throughput(benchmark):
    """Benchmark: L2 compression only"""
    state = _make_large_state(5000)
    # Run L1 first so L2 gets realistic input
    cleaned = GLOBAL_CONTEXT_PIPELINE._l1_cleanup(state)

    def run():
        return GLOBAL_CONTEXT_PIPELINE._l2_compress(cleaned)

    benchmark(run)


@pytest.mark.benchmark(group="token_estimate", min_rounds=100, warmup=True)
def test_state_token_count(benchmark):
    """Benchmark: token estimation performance"""
    state = _make_large_state(5000)

    def run():
        return _state_token_count(state)

    benchmark(run)


def test_pipeline_output_sanity():
    """Sanity check: benchmark test data is valid"""
    state = _make_large_state(5000)
    result = GLOBAL_CONTEXT_PIPELINE.process(state, workflow_id="sanity", model="deepseek-flash")
    tokens_before = _state_token_count(state)
    tokens_after = _state_token_count(result)
    assert isinstance(result, dict)
    assert tokens_after <= tokens_before, "Pipeline should not increase token count"
    assert "cache_hits" not in result, "L1 should remove diagnostic keys"
    assert len(result.get("errors", [])) <= 10, "L1 should truncate errors"
    print(f"\n  Sanity: {tokens_before} → {tokens_after} tokens "
          f"(ratio={tokens_after/tokens_before:.2f}, "
          f"saved={tokens_before - tokens_after})")


if __name__ == "__main__":
    test_pipeline_output_sanity()