"""
压缩策略基类 - 统一使用 SmartCrusher 本地压缩
所有策略共享 HeadroomEngine 单例，0 token 消耗
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CompressionResult:
    """压缩结果"""
    data: any
    ratio: float = 1.0
    quality_score: float = 1.0
    original_size: int = 0
    compressed_size: int = 0
    extra: dict = field(default_factory=dict)


class CompressionStrategy(ABC):
    """压缩策略基类 - 子类实现 compress 方法即可"""

    name: str = "base"

    @abstractmethod
    def compress(self, content, ratio: float = 0.3,
                 context: dict = None) -> CompressionResult:
        ...

    def estimate_tokens(self, text: str) -> int:
        cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        en_chars = len(text) - cn_chars
        return int(cn_chars * 1.5 + en_chars * 0.25)

    def _truncate_by_tokens(self, text: str, max_tokens: int) -> str:
        tokens = self.estimate_tokens(text)
        if tokens <= max_tokens:
            return text
        ratio = max_tokens / tokens
        keep_chars = int(len(text) * ratio)
        return text[:keep_chars] + "\n... [截断]"

    def _extract_keywords(self, text: str) -> set[str]:
        import re
        cn_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', text))
        en_words = set(re.findall(r'[a-zA-Z_]\w{2,}', text))
        return cn_words | en_words