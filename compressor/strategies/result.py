"""
查询结果压缩策略 - 委托 HeadroomEngine (SmartCrusher) 压缩
"""
from __future__ import annotations
from compressor.strategies.base import CompressionStrategy, CompressionResult
from compressor.engine import GLOBAL_HEADROOM


class ResultCompressor(CompressionStrategy):
    name = "sql_result"

    def compress(self, content, ratio: float = 0.10,
                 context: dict = None) -> CompressionResult:
        if not content:
            return CompressionResult(data=content or "", ratio=1.0)

        context = context or {}
        raw = str(content)

        result = GLOBAL_HEADROOM.compress("result", raw, context=context)
        compressed_data = result.data if result.ratio < 1.0 else content

        return CompressionResult(
            data=compressed_data,
            ratio=result.ratio,
            quality_score=result.quality_score,
            original_size=result.tokens_before,
            compressed_size=result.tokens_after,
        )