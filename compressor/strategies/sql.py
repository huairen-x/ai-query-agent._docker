"""
SQL 代码压缩策略 - 委托 HeadroomEngine (SmartCrusher) 压缩
"""
from __future__ import annotations
from compressor.strategies.base import CompressionStrategy, CompressionResult
from compressor.engine import GLOBAL_HEADROOM


class SQLCompressor(CompressionStrategy):
    name = "sql_code"

    def compress(self, content, ratio: float = 0.30,
                 context: dict = None) -> CompressionResult:
        if not content or not isinstance(content, str):
            return CompressionResult(data=content or "", ratio=1.0)

        context = context or {}
        result = GLOBAL_HEADROOM.compress("sql", content, context=context)

        return CompressionResult(
            data=result.data,
            ratio=result.ratio,
            quality_score=result.quality_score,
            original_size=result.tokens_before,
            compressed_size=result.tokens_after,
        )