"""
对话历史压缩策略 - 委托 HeadroomEngine (SmartCrusher) 压缩
"""
from __future__ import annotations
from compressor.strategies.base import CompressionStrategy, CompressionResult
from compressor.engine import GLOBAL_HEADROOM


class ConversationCompressor(CompressionStrategy):
    name = "conversation_history"

    def compress(self, content, ratio: float = 0.15,
                 context: dict = None) -> CompressionResult:
        if not content or not isinstance(content, list):
            return CompressionResult(data=content or [], ratio=1.0)

        context = context or {}
        conversation = list(content)

        if len(conversation) <= 4:
            return CompressionResult(data=conversation, ratio=1.0, quality_score=1.0)

        # 委托给 HeadroomEngine (SmartCrusher 本地压缩)
        raw = str(conversation)
        result = GLOBAL_HEADROOM.compress("conversation", raw, context=context)
        compressed_data = result.data if result.ratio < 1.0 else conversation

        return CompressionResult(
            data=compressed_data,
            ratio=result.ratio,
            quality_score=result.quality_score,
            original_size=result.tokens_before,
            compressed_size=result.tokens_after,
        )