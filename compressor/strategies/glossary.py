"""
业务术语表压缩策略 - 委托 HeadroomEngine (SmartCrusher) 压缩
"""
from __future__ import annotations
from compressor.strategies.base import CompressionStrategy, CompressionResult
from compressor.engine import GLOBAL_HEADROOM


class GlossaryCompressor(CompressionStrategy):
    name = "glossary"

    def compress(self, content, ratio: float = 0.25,
                 context: dict = None) -> CompressionResult:
        if not content:
            return CompressionResult(data=content or "", ratio=1.0)

        context = context or {}
        raw = str(content)

        result = GLOBAL_HEADROOM.compress("glossary", raw, context=context)
        compressed_data = result.data if result.ratio < 1.0 else content

        return CompressionResult(
            data=compressed_data,
            ratio=result.ratio,
            quality_score=result.quality_score,
            original_size=result.tokens_before,
            compressed_size=result.tokens_after,
        )