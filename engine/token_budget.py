"""
Token Budget Controller - 全局预算管理
监控每个环节消耗，预计算剩余预算，动态调整压缩率
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class BudgetAllocation:
    """一次 Agent 调用的预算分配"""
    system_prompt: int = 0
    history_context: int = 0
    input_data: int = 0
    reserved: int = 0

    @property
    def total(self) -> int:
        return self.system_prompt + self.history_context + self.input_data + self.reserved

    @property
    def usable(self) -> int:
        return self.system_prompt + self.history_context + self.input_data


@dataclass
class TokenBudget:
    """单个 Token 预算"""
    model: str
    max_context: int  # 模型最大上下文窗口
    safety_factor: float = 0.8  # 安全系数
    allocations: BudgetAllocation = field(default_factory=BudgetAllocation)

    @property
    def budget(self) -> int:
        return int(self.max_context * self.safety_factor)

    def allocate(self, system_prompt_pct: float = 0.20,
                 history_pct: float = 0.30,
                 input_pct: float = 0.40,
                 reserved_pct: float = 0.10) -> "TokenBudget":
        """按比例分配预算"""
        self.allocations = BudgetAllocation(
            system_prompt=int(self.budget * system_prompt_pct),
            history_context=int(self.budget * history_pct),
            input_data=int(self.budget * input_pct),
            reserved=int(self.budget * reserved_pct),
        )
        return self

    def get_target_ratio(self, content_type: str, current_tokens: int) -> float:
        """计算需要压缩到的目标比例"""
        if content_type == "system_prompt":
            budget_for = self.allocations.system_prompt
        elif content_type == "conversation_history":
            budget_for = self.allocations.history_context
        elif content_type in ("metadata", "sql_result", "sql_code", "glossary"):
            budget_for = self.allocations.input_data
        else:
            budget_for = self.allocations.input_data

        if current_tokens <= 0:
            return 1.0
        return max(0.05, min(1.0, budget_for / current_tokens))


class BudgetSnapshot:
    """预算快照 - 记录当前消耗状态"""
    def __init__(self):
        self.steps: list[dict] = []
        self.start_time = time.time()
        self.total_budget = 0
        self.consumed = 0

    def record_step(self, step_name: str, agent: str, tokens_used: int,
                    compression_ratio: float, model: str):
        self.steps.append({
            "step": step_name,
            "agent": agent,
            "tokens_used": tokens_used,
            "compression_ratio": compression_ratio,
            "model": model,
            "elapsed_ms": (time.time() - self.start_time) * 1000,
        })
        self.consumed += tokens_used

    @property
    def remaining(self) -> int:
        return max(0, self.total_budget - self.consumed)

    @property
    def consumption_pct(self) -> float:
        if self.total_budget <= 0:
            return 0.0
        return self.consumed / self.total_budget

    def summary(self) -> dict:
        return {
            "total_budget": self.total_budget,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "consumption_pct": round(self.consumption_pct * 100, 1),
            "steps": self.steps,
            "total_elapsed_ms": round((time.time() - self.start_time) * 1000, 1),
        }


class TokenBudgetController:
    """
    全局预算控制器
    - 为每个工作流实例分配预算
    - 监控各环节消耗
    - 动态调整后续环节的压缩率
    """
    MODEL_CONTEXTS = {
        "deepseek-flash": 4096,
        "deepseek-pro": 8192,
        "gpt-4o": 32768,
        "gpt-4o-mini": 8192,
        "claude-3-haiku": 8192,
        "claude-3-sonnet": 16384,
        "qwen-turbo": 4096,
        "qwen-plus": 8192,
    }

    def __init__(self):
        self._snapshots: dict[str, BudgetSnapshot] = {}

    def create_workflow_budget(self, workflow_id: str, model: str,
                               safety_factor: float = 0.8) -> TokenBudget:
        """创建工作流的预算"""
        max_ctx = self.MODEL_CONTEXTS.get(model, 4096)
        budget = TokenBudget(
            model=model,
            max_context=max_ctx,
            safety_factor=safety_factor,
        ).allocate()
        snapshot = BudgetSnapshot()
        snapshot.total_budget = budget.budget
        self._snapshots[workflow_id] = snapshot
        return budget

    def record(self, workflow_id: str, step_name: str, agent: str,
               tokens_used: int, compression_ratio: float, model: str):
        """记录一步的消耗"""
        snapshot = self._snapshots.get(workflow_id)
        if snapshot:
            snapshot.record_step(step_name, agent, tokens_used,
                                 compression_ratio, model)

    def get_adaptive_ratio(self, workflow_id: str, default_ratio: float) -> float:
        """
        根据剩余预算动态调整压缩率
        预算充足 → 降低压缩率（保留更多信息）
        预算紧张 → 提高压缩率（节省 Token）
        """
        snapshot = self._snapshots.get(workflow_id)
        if not snapshot or snapshot.total_budget <= 0:
            return default_ratio

        remaining_pct = snapshot.remaining / snapshot.total_budget
        steps_remaining = max(1, 6 - len(snapshot.steps))  # 预估剩余步骤

        if remaining_pct > 0.5:
            # 预算充足，降低压缩率（保留更多信息）
            return default_ratio * 1.2
        elif remaining_pct > 0.3:
            return default_ratio
        elif remaining_pct > 0.15:
            return default_ratio * 0.8
        else:
            # 预算紧张，提高压缩率
            return default_ratio * 0.5

    def get_snapshot(self, workflow_id: str) -> Optional[BudgetSnapshot]:
        return self._snapshots.get(workflow_id)

    def cleanup(self, workflow_id: str):
        self._snapshots.pop(workflow_id, None)


# 全局单例
GLOBAL_BUDGET_CONTROLLER = TokenBudgetController()