"""
三层上下文处理 Pipeline
L1: 规则删除无效段落 → L2: Headroom 压缩 → L3: LLM 摘要(默认关闭)
每层结束后检查是否已低于阈值，短路跳过后续层
"""
from __future__ import annotations
import time
from typing import Any, Optional
from dataclasses import dataclass, field

from engine.config import GLOBAL_CONFIG
from compressor.cleanup import GLOBAL_CONTEXT_CLEANER
from compressor.engine import GLOBAL_HEADROOM
from engine.token_budget import GLOBAL_BUDGET_CONTROLLER


@dataclass
class PipelineResult:
    """Pipeline 处理结果"""
    state: dict = field(default_factory=dict)
    layers_applied: list[str] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    threshold_a: int = 0
    threshold_b: int = 0
    elapsed_ms: float = 0.0


def _estimate_tokens(text: str) -> int:
    """估算 token 数（中英文混合）"""
    if not text:
        return 0
    cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    en_chars = len(text) - cn_chars
    return int(cn_chars * 1.5 + en_chars * 0.25)


def _state_token_count(state: dict) -> int:
    """估算整个 state dict 的 token 数"""
    return _estimate_tokens(str(state))


class ContextPipeline:
    """
    三层上下文处理 Pipeline

    L1: 规则删除 — 去空、截断、过滤诊断信息，零 token 消耗
    L2: Headroom 压缩 — 按字段类型调用 SmartCrusher
    L3: LLM 摘要 — 对剩余非结构化内容做摘要（默认关闭）
    """

    def __init__(self):
        self._stats = {"processed": 0, "l1_only": 0, "l2_only": 0, "l3_used": 0}

    def process(
        self,
        state: dict,
        workflow_id: str = "",
        model: str = "deepseek-flash",
    ) -> dict:
        """
        执行三层上下文处理

        Args:
            state: 工作流输出的原始 state
            workflow_id: 工作流 ID（用于 TokenBudget 跟踪）
            model: 模型名称（用于获取预算窗口大小）

        Returns:
            处理后的 state
        """
        start = time.time()
        tokens_before = _state_token_count(state)

        # 获取阈值
        budget = GLOBAL_BUDGET_CONTROLLER.create_workflow_budget(
            workflow_id, model
        )
        threshold_a = budget.allocations.usable  # L1 目标
        threshold_b = budget.allocations.input_data  # L2 目标

        result = PipelineResult(
            state=state,
            tokens_before=tokens_before,
            threshold_a=threshold_a,
            threshold_b=threshold_b,
        )

        # 如果原始 state 已经低于阈值 A，直接返回（无需处理）
        if tokens_before <= threshold_a:
            result.state = state
            result.layers_applied = []
            result.tokens_after = tokens_before
            result.elapsed_ms = (time.time() - start) * 1000
            self._stats["processed"] += 1
            self._stats["l1_only"] += 1
            return result.state

        # ── L1: 规则删除 ────────────────────────────────────
        state = self._l1_cleanup(state)
        tokens_after_l1 = _state_token_count(state)
        result.layers_applied.append("L1")

        if tokens_after_l1 <= threshold_a:
            result.state = state
            result.tokens_after = tokens_after_l1
            result.elapsed_ms = (time.time() - start) * 1000
            self._stats["processed"] += 1
            self._stats["l1_only"] += 1
            return result.state

        # ── L2: Headroom 压缩 ──────────────────────────────
        state = self._l2_compress(state)
        tokens_after_l2 = _state_token_count(state)
        result.layers_applied.append("L2")

        if tokens_after_l2 <= threshold_b:
            result.state = state
            result.tokens_after = tokens_after_l2
            result.elapsed_ms = (time.time() - start) * 1000
            self._stats["processed"] += 1
            self._stats["l2_only"] += 1
            return result.state

        # ── L3: LLM 摘要（默认关闭） ───────────────────────
        if GLOBAL_CONFIG.context_cleanup.l3_enabled:
            state = self._l3_summarize(state)
            result.layers_applied.append("L3")
            self._stats["l3_used"] += 1

        result.state = state
        result.tokens_after = _state_token_count(state)
        result.elapsed_ms = (time.time() - start) * 1000
        self._stats["processed"] += 1
        return result.state

    # ── L1: 规则删除 ────────────────────────────────────────

    def _l1_cleanup(self, state: dict) -> dict:
        """
        L1 规则删除 — 零 token 消耗

        规则:
        1. 移除所有值为 None/空字符串/空列表/空字典 的字段
        2. errors 列表超过 10 条时裁剪到最近 10 条
        3. query_result.rows 超过 50 行时截断
        4. metadata 中表数量超过 10 张时截断
        5. audit_trail 超过 20 条时截断
        6. 移除 cache_hits 等诊断信息
        7. 堆栈跟踪截断到 500 字符
        """
        cleaned = {}

        for key, value in state.items():
            # 跳过空值
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, dict)) and not value:
                continue

            # 移除诊断信息
            if key in ("cache_hits", "result_cache_hit", "metadata_cache_hit"):
                continue

            # 处理特定字段
            if key == "errors" and isinstance(value, list):
                if len(value) > 10:
                    cleaned[key] = value[-10:]  # 保留最近 10 条
                else:
                    cleaned[key] = value
                continue

            if key == "audit_trail" and isinstance(value, list):
                if len(value) > 20:
                    cleaned[key] = value[-20:]  # 保留最近 20 条
                else:
                    cleaned[key] = value
                continue

            if key == "query_result" and isinstance(value, dict):
                rows = value.get("rows", [])
                if isinstance(rows, list) and len(rows) > 50:
                    value = dict(value)
                    value["rows"] = rows[:50]
                    value["row_count"] = len(rows[:50])
                    value["_truncated"] = True
                cleaned[key] = value
                continue

            if key == "metadata" and isinstance(value, dict):
                tables = value.get("tables", [])
                if isinstance(tables, list) and len(tables) > 10:
                    value = dict(value)
                    value["tables"] = tables[:10]
                    value["_truncated"] = True
                cleaned[key] = value
                continue

            # 通用处理：堆栈跟踪截断
            if isinstance(value, str) and len(value) > 500:
                # 检查是否包含堆栈跟踪
                if "Traceback" in value or "File \"" in value:
                    cleaned[key] = value[:500] + "...[truncated]"
                    continue

            cleaned[key] = value

        return cleaned

    # ── L2: Headroom 压缩 ──────────────────────────────────

    # state 字段到 content_type 的映射
    FIELD_TO_CONTENT_TYPE = {
        "metadata": "metadata",
        "query_result": "sql_result",
        "sql": "sql_code",
        "interpretation": "conversation",
        "question": "conversation",
        "cleanup_result": "conversation",
        "intent": "conversation",
        "chart_suggestion": "conversation",
    }

    def _l2_compress(self, state: dict) -> dict:
        """
        L2 Headroom 压缩 — 按字段类型调用 SmartCrusher
        结构化字段（list/dict）直接传，字符串字段传文本
        """
        compressed = {}

        for key, value in state.items():
            content_type = self.FIELD_TO_CONTENT_TYPE.get(key, "conversation")

            if isinstance(value, str) and len(value) > 50:
                result = GLOBAL_HEADROOM.compress(content_type, value)
                compressed[key] = result.data if result.ratio < 1.0 else value
            elif isinstance(value, (list, dict)) and value:
                result = GLOBAL_HEADROOM.compress(content_type, value)
                compressed[key] = result.data if result.ratio < 1.0 else value
            else:
                compressed[key] = value

        return compressed

    # ── L3: LLM 摘要（预留接口，默认关闭） ─────────────────

    def _l3_summarize(self, state: dict) -> dict:
        """
        L3 LLM 摘要 — 对剩余非结构化大块内容做摘要
        默认关闭（l3_enabled=False），此方法仅做占位
        """
        # 占位：后续可集成 LLM 调用
        # 当前只是标记已应用 L3
        state["_l3_applied"] = True
        return state

    def get_stats(self) -> dict:
        return dict(self._stats)


# 全局单例
GLOBAL_CONTEXT_PIPELINE = ContextPipeline()