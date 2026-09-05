"""
上下文清理器 - Headroom 压缩前的预处理步骤
去重/去空/裁剪/过滤/归一化，减少无效 Token 占用
"""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CleanupResult:
    """清理结果"""
    original_size: int = 0
    cleaned_size: int = 0
    removed_empty: int = 0
    removed_duplicates: int = 0
    truncated_count: int = 0
    filtered_count: int = 0
    ratio: float = 1.0
    elapsed_ms: float = 0.0


class ContextCleaner:
    """
    上下文清理器
    在 Headroom 压缩前执行，主动移除无效内容

    清理策略:
    - 去重：移除完全重复的条目
    - 去空：移除空内容/null 条目
    - 裁剪：单条消息超过阈值截断
    - 过滤：移除无关系统消息/噪音
    - 归一化：格式化统一
    """

    # 需要过滤的系统消息关键词
    FILTER_KEYWORDS = [
        "你现在是一个", "你是一个", "作为AI", "作为助手",
        "system prompt", "system_message", "你叫",
        "behaved", "guidelines", "rules",
    ]

    # 单条消息最大字符数
    MAX_MESSAGE_LENGTH = 2000

    def __init__(self):
        self._stats = {"cleanup_count": 0, "total_saved_tokens": 0}

    def cleanup_conversation(self, messages: list[dict]) -> tuple[list[dict], CleanupResult]:
        """
        清理对话历史

        Args:
            messages: 对话消息列表

        Returns:
            (cleaned_messages, CleanupResult)
        """
        start = time.time()
        result = CleanupResult()
        if not messages:
            return [], result

        original = list(messages)
        result.original_size = len(str(original))

        cleaned = []
        seen = set()

        for msg in original:
            content = msg.get("content", "")
            role = msg.get("role", "")

            # 1. 去空
            if not content or not content.strip():
                result.removed_empty += 1
                continue

            # 2. 去重
            content_stripped = content.strip()
            if content_stripped in seen:
                result.removed_duplicates += 1
                continue
            seen.add(content_stripped)

            # 3. 过滤系统噪音
            if self._should_filter(content_stripped, role):
                result.filtered_count += 1
                continue

            # 4. 裁剪超长消息
            if len(content_stripped) > self.MAX_MESSAGE_LENGTH:
                msg = dict(msg)
                msg["content"] = content_stripped[:self.MAX_MESSAGE_LENGTH] + "..."
                msg["_truncated"] = True
                result.truncated_count += 1

            cleaned.append(msg)

        result.cleaned_size = len(str(cleaned))
        result.ratio = result.cleaned_size / result.original_size if result.original_size else 1.0
        result.elapsed_ms = (time.time() - start) * 1000

        self._stats["cleanup_count"] += 1
        self._stats["total_saved_tokens"] += (result.original_size - result.cleaned_size) // 4

        return cleaned, result

    def cleanup_metadata(self, metadata: dict) -> tuple[dict, CleanupResult]:
        """
        清理元数据
        - 移除空字段
        - 限制字段数量
        """
        start = time.time()
        result = CleanupResult()
        original_str = str(metadata)
        result.original_size = len(original_str)

        cleaned = {}
        for k, v in metadata.items():
            if v is None or (isinstance(v, (list, dict)) and not v):
                result.removed_empty += 1
                continue
            if isinstance(v, list) and len(v) > 50:
                cleaned[k] = v[:50]
                result.truncated_count += 1
            else:
                cleaned[k] = v

        result.cleaned_size = len(str(cleaned))
        result.ratio = result.cleaned_size / result.original_size if result.original_size else 1.0
        result.elapsed_ms = (time.time() - start) * 1000
        return cleaned, result

    def cleanup_sql(self, sql: str) -> tuple[str, CleanupResult]:
        """
        清理 SQL
        - 移除注释
        - 压缩多余空格
        - 统一关键字大小写
        """
        start = time.time()
        result = CleanupResult()
        result.original_size = len(sql)

        cleaned = sql

        # 移除单行注释
        cleaned = re.sub(r'--[^\n]*', '', cleaned)
        # 移除多行注释
        cleaned = re.sub(r'/\*[\s\S]*?\*/', '', cleaned)
        # 压缩空格
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        # 统一关键字大写
        keywords = r'\b(select|from|where|and|or|not|in|like|between|is|null|as|on|join|left|right|inner|outer|cross|full|group|by|having|order|asc|desc|limit|offset|union|all|distinct|case|when|then|else|end|with|recursive|insert|into|values|update|set|delete|create|table|drop|alter|add|column|index|view|grant|revoke)\b'
        cleaned = re.sub(keywords, lambda m: m.group(1).upper(), cleaned, flags=re.IGNORECASE)

        result.cleaned_size = len(cleaned)
        result.ratio = result.cleaned_size / result.original_size if result.original_size else 1.0
        result.elapsed_ms = (time.time() - start) * 1000
        return cleaned, result

    def _should_filter(self, content: str, role: str) -> bool:
        """判断是否应该过滤该消息"""
        content_lower = content.lower()
        for kw in self.FILTER_KEYWORDS:
            if kw in content_lower:
                return True
        return False

    def get_stats(self) -> dict:
        return dict(self._stats)


GLOBAL_CONTEXT_CLEANER = ContextCleaner()