"""Unit tests for TokenBudget and TokenBudgetController"""
from __future__ import annotations
import pytest
from engine.token_budget import (
    TokenBudget,
    TokenBudgetController,
    BudgetAllocation,
    GLOBAL_BUDGET_CONTROLLER,
)


class TestBudgetAllocation:
    """BudgetAllocation 基础属性"""

    def test_total(self):
        a = BudgetAllocation(system_prompt=100, history_context=200, input_data=300, reserved=50)
        assert a.total == 650

    def test_usable(self):
        a = BudgetAllocation(system_prompt=100, history_context=200, input_data=300, reserved=50)
        assert a.usable == 600  # total - reserved


class TestTokenBudget:
    """TokenBudget 分配与阈值"""

    def test_budget_with_safety_factor(self):
        b = TokenBudget(model="test", max_context=10000, safety_factor=0.8)
        assert b.budget == 8000

    def test_default_allocation(self):
        b = TokenBudget(model="test", max_context=10000).allocate()
        # default: 20% system, 30% history, 40% input, 10% reserved
        assert b.allocations.system_prompt == 1600  # 8000 * 0.20
        assert b.allocations.history_context == 2400  # 8000 * 0.30
        assert b.allocations.input_data == 3200  # 8000 * 0.40
        assert b.allocations.reserved == 800  # 8000 * 0.10

    def test_custom_allocation(self):
        b = TokenBudget(model="test", max_context=10000).allocate(0.5, 0.2, 0.2, 0.1)
        assert b.allocations.system_prompt == 4000

    def test_get_threshold_a_is_usable(self):
        b = TokenBudget(model="test", max_context=10000).allocate(0.2, 0.3, 0.4, 0.1)
        # usable = system_prompt + history_context + input_data = budget * (0.2+0.3+0.4) = 8000*0.9
        assert b.get_threshold_a() == b.allocations.usable == 7200

    def test_get_threshold_b_is_input_data(self):
        b = TokenBudget(model="test", max_context=10000).allocate(0.2, 0.3, 0.4, 0.1)
        assert b.get_threshold_b() == b.allocations.input_data == 3200

    def test_get_target_ratio(self):
        b = TokenBudget(model="test", max_context=10000).allocate()
        ratio = b.get_target_ratio("conversation_history", 5000)
        # budget_for = history_context = 2400, current_tokens = 5000
        assert 0.0 < ratio <= 1.0
        assert ratio == pytest.approx(2400 / 5000, rel=0.01)


class TestTokenBudgetController:
    """TokenBudgetController 管理"""

    def test_create_workflow_budget(self):
        budget = GLOBAL_BUDGET_CONTROLLER.create_workflow_budget("wf-1", "deepseek-flash")
        assert budget.model == "deepseek-flash"
        assert budget.max_context == 4096
        assert budget.budget == int(4096 * 0.8)

    def test_record_and_snapshot(self):
        GLOBAL_BUDGET_CONTROLLER.create_workflow_budget("wf-2", "deepseek-flash")
        GLOBAL_BUDGET_CONTROLLER.record("wf-2", "step1", "agent1", 500, 0.5, "deepseek-flash")
        snap = GLOBAL_BUDGET_CONTROLLER.get_snapshot("wf-2")
        assert snap is not None
        assert snap.consumed == 500
        assert len(snap.steps) == 1

    def test_cleanup_removes_snapshot(self):
        GLOBAL_BUDGET_CONTROLLER.create_workflow_budget("wf-3", "deepseek-flash")
        GLOBAL_BUDGET_CONTROLLER.cleanup("wf-3")
        assert GLOBAL_BUDGET_CONTROLLER.get_snapshot("wf-3") is None

    def test_adaptive_ratio_with_remaining_budget(self):
        GLOBAL_BUDGET_CONTROLLER.create_workflow_budget("wf-4", "deepseek-flash")
        # Record a small consumption so remaining is high
        GLOBAL_BUDGET_CONTROLLER.record("wf-4", "step1", "agent1", 100, 0.5, "deepseek-flash")
        ratio = GLOBAL_BUDGET_CONTROLLER.get_adaptive_ratio("wf-4", 0.3)
        # remaining > 50% → ratio * 1.2
        assert ratio == pytest.approx(0.3 * 1.2, rel=0.01)

    def test_adaptive_ratio_low_budget(self):
        GLOBAL_BUDGET_CONTROLLER.create_workflow_budget("wf-5", "deepseek-flash")
        # Consume most of the budget
        GLOBAL_BUDGET_CONTROLLER.record("wf-5", "step1", "agent1", 3000, 0.5, "deepseek-flash")
        ratio = GLOBAL_BUDGET_CONTROLLER.get_adaptive_ratio("wf-5", 0.3)
        # remaining < 0.15 → ratio * 0.5
        assert ratio == pytest.approx(0.3 * 0.5, rel=0.01)

    def test_model_contexts_contains_expected_models(self):
        assert "deepseek-flash" in TokenBudgetController.MODEL_CONTEXTS
        assert "deepseek-pro" in TokenBudgetController.MODEL_CONTEXTS
        assert "gpt-4o" in TokenBudgetController.MODEL_CONTEXTS