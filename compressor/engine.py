"""
Headroom Engine - SmartCrusher 本地压缩 + Proxy 统计上报
- 使用 SmartCrusher (Rust-backed) 本地压缩 JSON 数组（0 token 消耗）
- 后台异步上报统计到 headroom proxy dashboard
- 支持上下文预清理（cleanup）→ 压缩 → 统计全链路
"""
from __future__ import annotations
import os
import json
import time
import urllib.request
import urllib.error
import threading
from dataclasses import dataclass, field
from typing import Optional

PROXY_BASE_URL = os.environ.get("HEADROOM_PROXY_URL", "http://localhost:8787")
PROXY_CHAT_URL = f"{PROXY_BASE_URL}/v1/chat/completions"


@dataclass
class CompressionResult:
    """压缩结果"""
    data: any = None
    ratio: float = 1.0
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    quality_score: float = 1.0
    elapsed_ms: float = 0.0
    strategy: str = "smart_crusher"


@dataclass
class QualityMetrics:
    """压缩质量指标"""
    original_tokens: int = 0
    compressed_tokens: int = 0
    compression_ratio: float = 1.0
    quality_score: float = 1.0
    elapsed_ms: float = 0.0


class HeadroomEngine:
    """
    Headroom 压缩引擎
    - SmartCrusher (Rust) 本地压缩，无需 API 调用
    - 后台异步上报统计到 proxy dashboard
    - 支持分块压缩、质量监控
    """

    def __init__(self):
        self.enabled = True
        self._stats = {
            "compressions": 0,
            "total_original_tokens": 0,
            "total_compressed_tokens": 0,
            "total_saved_tokens": 0,
        }
        self._metrics: list[QualityMetrics] = []
        self._crusher = None
        self._init_crusher()

    # ── SmartCrusher 初始化 ──────────────────────────────────

    def _init_crusher(self):
        """初始化 SmartCrusher (Rust-backed, 0 token 消耗)"""
        try:
            from headroom.transforms.smart_crusher import (
                SmartCrusher,
                SmartCrusherConfig,
            )
            self._crusher = SmartCrusher(
                SmartCrusherConfig(
                    enabled=True,
                    min_items_to_analyze=3,
                    min_tokens_to_crush=100,
                    max_items_after_crush=15,
                    variance_threshold=2.0,
                    uniqueness_threshold=0.1,
                    similarity_threshold=0.8,
                    preserve_change_points=True,
                    dedup_identical_items=True,
                    first_fraction=0.3,
                    last_fraction=0.15,
                    lossless_only=False,
                ),
                with_compaction=True,
            )
            self._smart_crusher_available = True
        except ImportError:
            self._crusher = None
            self._smart_crusher_available = False

    # ── 统一压缩入口 ────────────────────────────────────────

    def compress(self, content_type: str, content, workflow_id: str = "",
                 context: dict = None) -> CompressionResult:
        """
        统一压缩入口

        Args:
            content_type: 内容类型标识 (conversation / metadata / sql / result / prompt / glossary)
            content: 要压缩的内容 (str / list / dict)
            workflow_id: 工作流ID
            context: 额外上下文

        Returns:
            CompressionResult
        """
        if not self.enabled:
            return CompressionResult(data=content, ratio=1.0)

        text = str(content) if not isinstance(content, str) else content
        if not text.strip():
            return CompressionResult(data=content, ratio=1.0)

        start = time.time()
        tokens_before = self._estimate_tokens(text)

        # SmartCrusher 本地压缩
        compressed_text = self._smart_crusher_compress(text, content_type)

        elapsed = (time.time() - start) * 1000
        tokens_after = self._estimate_tokens(compressed_text)
        ratio = tokens_after / tokens_before if tokens_before > 0 else 1.0

        self._stats["compressions"] += 1
        self._stats["total_original_tokens"] += tokens_before
        self._stats["total_compressed_tokens"] += tokens_after
        self._stats["total_saved_tokens"] += (tokens_before - tokens_after)

        self._metrics.append(QualityMetrics(
            original_tokens=tokens_before,
            compressed_tokens=tokens_after,
            compression_ratio=ratio,
            elapsed_ms=elapsed,
        ))

        # 后台异步上报统计到 proxy dashboard
        self._report_stats_async(text, compressed_text, content_type)

        strategy = "smart_crusher" if compressed_text != text else "none"
        return CompressionResult(
            data=compressed_text,
            ratio=ratio,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_before - tokens_after,
            quality_score=ratio,
            elapsed_ms=elapsed,
            strategy=strategy,
        )

    # ── SmartCrusher 本地压缩 ───────────────────────────────

    def _smart_crusher_compress(self, text: str, content_type: str = "") -> str:
        """
        使用 SmartCrusher (Rust) 本地压缩内容
        - 将文本按行拆分为 JSON 数组 (tool-result 格式)
        - SmartCrusher 自动去重、聚类、压缩结构化数据
        """
        if not text.strip() or not self._crusher:
            return text

        try:
            # 构建 JSON 数组字符串 (SmartCrusher.crush 需要 JSON 字符串)
            lines = text.strip().split("\n")
            if len(lines) < 3:
                return text

            # 构建 JSON 数组: 每行作为一个 tool-result 对象
            json_items = []
            for line in lines:
                if line.strip():
                    item = {"role": "tool", "content": line.strip()}
                    json_items.append(item)

            if len(json_items) < 3:
                return text

            content_json = json.dumps(json_items, ensure_ascii=False)

            # 调用 SmartCrusher.crush(content_str, query)
            result = self._crusher.crush(content_json, query=content_type)

            if result.was_modified and result.compressed:
                # 解析压缩后的 JSON 数组，提取 content
                try:
                    compressed_items = json.loads(result.compressed)
                    if isinstance(compressed_items, list):
                        parts = []
                        for item in compressed_items:
                            if isinstance(item, dict):
                                parts.append(item.get("content", json.dumps(item, ensure_ascii=False)))
                            else:
                                parts.append(str(item))
                        return "\n".join(parts)
                except (json.JSONDecodeError, TypeError):
                    pass
                return result.compressed
            return text

        except Exception:
            return text

    # ── Proxy 统计上报 ─────────────────────────────────────

    def _report_stats_async(self, original: str, compressed: str, content_type: str):
        """后台线程上报压缩统计到 proxy dashboard"""
        try:
            t = threading.Thread(
                target=self._do_report_stats,
                args=(original, compressed, content_type),
                daemon=True,
            )
            t.start()
        except Exception:
            pass

    def _do_report_stats(self, original: str, compressed: str, content_type: str):
        """
        向 headroom proxy 发送工具结果格式的请求
        Proxy 会记录统计信息，dashboard 可见
        """
        try:
            items = [
                {"role": "tool", "content": f"[{content_type}] {original[:200]}"},
                {"role": "tool", "content": f"[compressed] {compressed[:200]}"},
            ]
            body = json.dumps({
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
                ],
                "max_tokens": 1,
                "stream": False,
            }).encode("utf-8")

            req = urllib.request.Request(
                PROXY_CHAT_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)

        except Exception:
            pass

    # ── Token 估算 ─────────────────────────────────────────

    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数（中英文混合）"""
        if not text:
            return 0
        cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        en_chars = len(text) - cn_chars
        return int(cn_chars * 1.5 + en_chars * 0.25)

    # ── 统计查询 ───────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取本地压缩统计"""
        s = self._stats
        total_original = s["total_original_tokens"]
        avg_ratio = round(s["total_compressed_tokens"] / total_original, 4) if total_original else 1.0

        avg_quality = 1.0
        avg_elapsed = 0.0
        if self._metrics:
            avg_quality = round(
                sum(m.quality_score for m in self._metrics) / len(self._metrics), 4
            )
            avg_elapsed = round(
                sum(m.elapsed_ms for m in self._metrics) / len(self._metrics), 2
            )

        return {
            "compressions": s["compressions"],
            "total_original_tokens": s["total_original_tokens"],
            "total_compressed_tokens": s["total_compressed_tokens"],
            "total_saved_tokens": s["total_saved_tokens"],
            "avg_compression_ratio": avg_ratio,
            "avg_quality_score": avg_quality,
            "avg_elapsed_ms": avg_elapsed,
            "smart_crusher_available": self._smart_crusher_available,
            "proxy_url": PROXY_BASE_URL,
            "proxy_dashboard": f"{PROXY_BASE_URL}/dashboard",
        }

    def get_proxy_stats(self) -> dict:
        """从 headroom proxy 获取统计信息"""
        try:
            req = urllib.request.Request(f"{PROXY_BASE_URL}/stats", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {"error": "proxy not available"}

    def reset_stats(self):
        """重置统计"""
        self._stats = {
            "compressions": 0,
            "total_original_tokens": 0,
            "total_compressed_tokens": 0,
            "total_saved_tokens": 0,
        }
        self._metrics.clear()


# 全局单例
GLOBAL_HEADROOM = HeadroomEngine()