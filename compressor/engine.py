"""
Headroom Engine - SmartCrusher 本地压缩 + Proxy 统计上报
- 使用 SmartCrusher (Rust-backed) 本地压缩 JSON 数组（0 token 消耗）
- 后台异步上报统计到 headroom proxy dashboard
- 支持上下文预清理（cleanup）→ 压缩 → 统计全链路
"""
from __future__ import annotations
import os
import json
import queue
import time
import urllib.request
import urllib.error
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

PROXY_BASE_URL = os.environ.get("HEADROOM_PROXY_URL", "http://localhost:8787")
PROXY_CHAT_URL = f"{PROXY_BASE_URL}/v1/chat/completions"

# 统计上报走固定数量守护线程 + 有界队列，避免每次压缩都新建线程
_REPORT_QUEUE: "queue.Queue" = queue.Queue(maxsize=64)
_REPORT_WORKERS = int(os.environ.get("HEADROOM_REPORT_WORKERS", "2"))
_report_lock = threading.Lock()
_report_started = False


def _report_worker():
    """消费上报队列；异常不影响后续任务"""
    while True:
        original, compressed, content_type = _REPORT_QUEUE.get()
        try:
            GLOBAL_HEADROOM._do_report_stats(original, compressed, content_type)
        except Exception:
            pass
        finally:
            _REPORT_QUEUE.task_done()


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
        self.enabled = os.environ.get("HEADROOM_ENABLED", "true").lower() == "true"
        self.report_enabled = os.environ.get("HEADROOM_REPORT_ENABLED", "true").lower() == "true"
        self._lock = threading.Lock()
        self._stats = {
            "compressions": 0,
            "total_original_tokens": 0,
            "total_compressed_tokens": 0,
            "total_saved_tokens": 0,
            "reports_dropped": 0,
        }
        # 只保留最近 1000 次质量指标，防止长跑内存无界增长
        self._metrics: deque[QualityMetrics] = deque(maxlen=1000)
        # 供审计取"某步骤期间发生的压缩比"：单调序号 + 最近比值
        self._ratio_seq = 0
        self._recent_ratios: deque[tuple[int, float]] = deque(maxlen=512)
        self._crusher = None
        self._init_crusher()

    def mark(self) -> int:
        """记录压缩比观测点；配合 ratios_since() 取该区间内的压缩比"""
        with self._lock:
            return self._ratio_seq

    def ratios_since(self, mark: int) -> list[float]:
        """返回 mark 之后每次压缩的 ratio（压缩后/压缩前 token 比，越小省得越多）"""
        with self._lock:
            return [ratio for seq, ratio in self._recent_ratios if seq > mark]

    # ── SmartCrusher 初始化 ──────────────────────────────────

    def _init_crusher(self):
        """初始化 SmartCrusher (Rust-backed, 0 token 消耗)"""
        try:
            from headroom.transforms.smart_crusher import (
                SmartCrusher,
                SmartCrusherConfig,
            )
            # 阈值可配；默认调低以覆盖真实小负载（元数据/小结果集）
            min_items = int(os.environ.get("HEADROOM_MIN_ITEMS", "2"))
            min_tokens = int(os.environ.get("HEADROOM_MIN_TOKENS", "20"))
            max_items = int(os.environ.get("HEADROOM_MAX_ITEMS_AFTER_CRUSH", "15"))
            self._crusher = SmartCrusher(
                SmartCrusherConfig(
                    enabled=True,
                    min_items_to_analyze=min_items,
                    min_tokens_to_crush=min_tokens,
                    max_items_after_crush=max_items,
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

        is_structured = isinstance(content, (dict, list))
        text = (json.dumps(content, ensure_ascii=False, default=str)
                if is_structured
                else (content if isinstance(content, str) else str(content)))
        if not text.strip():
            return CompressionResult(data=content, ratio=1.0)

        start = time.time()
        tokens_before = self._estimate_tokens(text)

        compressed_text = self._smart_crusher_compress(
            content, text, is_structured, content_type)

        elapsed = (time.time() - start) * 1000
        tokens_after = self._estimate_tokens(compressed_text)
        ratio = tokens_after / tokens_before if tokens_before > 0 else 1.0

        # 仅当真实节省才采纳压缩；否则按未压缩处理，
        # 避免 SmartCrusher 对高基数/不相似数据反而膨胀、污染结构化 data
        if tokens_after >= tokens_before or not compressed_text.strip():
            compressed_text = text
            tokens_after = tokens_before
            ratio = 1.0

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

        with self._lock:
            self._ratio_seq += 1
            self._recent_ratios.append((self._ratio_seq, ratio))

        self._report_stats_async(text, compressed_text, content_type)

        if is_structured:
            try:
                compressed_data = json.loads(compressed_text)
            except (json.JSONDecodeError, TypeError):
                # 压缩把结构化内容拍平成字符串（无法回解析成 JSON）。
                # 为避免破坏下游功能字段（如 query_result.rows / metadata），
                # 返回原始结构作为 data；压缩节省仍计入统计并上报 proxy。
                compressed_data = content
        else:
            compressed_data = compressed_text

        strategy = "smart_crusher" if compressed_text != text else "none"
        return CompressionResult(
            data=compressed_data,
            ratio=ratio,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            tokens_saved=tokens_before - tokens_after,
            quality_score=self._estimate_fidelity(text, compressed_text),
            elapsed_ms=elapsed,
            strategy=strategy,
        )

    # ── SmartCrusher 本地压缩 ───────────────────────────────

    def _build_crush_items(self, content, is_structured: bool, text: str) -> list:
        """
        把待压缩内容拆成 SmartCrusher 需要的 tool-result item 列表。
        - 结构化 list: 每个元素独立成一个 item（保留语义边界），避免单行化吞并
        - 结构化 dict: 逐 key/value 拆；值为 list 时其每个元素独立成 item
          （覆盖 sql_result 的 {"rows": [...]}、metadata 的多表 schema）
        - 纯文本: 按行拆分（sql / prompt / 日志等）
        """
        items = []

        def _push(payload: str):
            if payload is None:
                return
            if not isinstance(payload, str):
                payload = json.dumps(payload, ensure_ascii=False, default=str)
            if payload.strip():
                items.append({"role": "tool", "content": payload.strip()})

        if is_structured and isinstance(content, list):
            for el in content:
                if el is None:
                    continue
                if isinstance(el, dict) and isinstance(el.get("content"), str):
                    _push(el.get("content"))
                else:
                    _push(json.dumps(el, ensure_ascii=False, default=str))
        elif is_structured and isinstance(content, dict):
            for k, v in content.items():
                if isinstance(v, list):
                    for el in v:
                        _push(json.dumps(el, ensure_ascii=False, default=str))
                elif isinstance(v, dict):
                    _push(json.dumps({k: v}, ensure_ascii=False, default=str))
                else:
                    _push(f"{k}: {v}")
        else:
            for line in text.split("\n"):
                s = line.strip()
                if s:
                    items.append({"role": "tool", "content": s})
        return items

    def _smart_crusher_compress(self, content, text: str, is_structured: bool,
                                content_type: str = "") -> str:
        """
        使用 SmartCrusher (Rust) 本地压缩内容
        - 结构化内容按元素、纯文本按行拆分为 JSON 数组 (tool-result 格式)
        - SmartCrusher 自动去重、聚类、压缩结构化数据
        """
        if not text.strip() or not self._crusher:
            return text

        try:
            json_items = self._build_crush_items(content, is_structured, text)
            min_items = int(os.environ.get("HEADROOM_MIN_ITEMS", "2"))
            if len(json_items) < min_items:
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
        """把统计上报投递到有界队列，由固定守护线程消费"""
        global _report_started
        if not self.report_enabled:
            return
        if not _report_started:
            with _report_lock:
                if not _report_started:
                    for index in range(_REPORT_WORKERS):
                        threading.Thread(
                            target=_report_worker,
                            name=f"headroom-report-{index}",
                            daemon=True,
                        ).start()
                    _report_started = True
        try:
            _REPORT_QUEUE.put_nowait((original, compressed, content_type))
        except queue.Full:
            with self._lock:
                self._stats["reports_dropped"] += 1

    def _do_report_stats(self, original: str, compressed: str, content_type: str):
        """
        向 headroom proxy 发送工具结果格式的请求
        Proxy 会记录统计信息，dashboard 可见。

        去重上报：仅当本地确实压缩（original != compressed）时才同时发送
        原始+压缩两份，避免 proxy 因两份相同全量内容去重而产生虚高节省；
        本地未压缩时只发一份，让 dashboard 百分比如实反映真实节省。
        """
        try:
            max_chars = int(os.environ.get("HEADROOM_REPORT_MAX_CHARS", "2000000"))
            orig_body = original[:max_chars]
            comp_body = compressed[:max_chars]

            if compressed != original:
                items = [
                    {"role": "tool", "content": f"[{content_type}][original] {orig_body}"},
                    {"role": "tool", "content": f"[{content_type}][compressed] {comp_body}"},
                ]
            else:
                items = [
                    {"role": "tool", "content": f"[{content_type}][no_compression] {orig_body}"},
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
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = getattr(resp, "status", resp.getcode())
                if os.environ.get("HEADROOM_DEBUG", "true").lower() == "true":
                    tag = "compressed" if compressed != original else "no_compression"
                    print(f"[headroom-report] OK {content_type}[{tag}] status={status} orig={len(orig_body)}B", flush=True)

        except Exception as e:
            print(f"[headroom-report] FAIL {content_type} {PROXY_CHAT_URL}: {type(e).__name__}: {e}", flush=True)

    # ── Token 估算 ─────────────────────────────────────────

    def _estimate_fidelity(self, original: str, compressed: str) -> float:
        """估算压缩后内容对原始内容的语义保留度 (0~1，越高越接近原始)

        - 未压缩时恒为 1.0
        - 压缩时用字符 bigram 重叠度衡量保留的信息比例，
          与压缩激进程度解耦（激进但保真 => 高分）
        """
        if original == compressed:
            return 1.0
        if not original:
            return 0.0

        def grams(s: str):
            if len(s) < 2:
                return set(s) or {""}
            return {s[i:i + 2] for i in range(len(s) - 1)}

        a = grams(original)
        if not a:
            return 0.0
        b = grams(compressed)
        overlap = a & b
        return round(len(overlap) / len(a), 4)

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
            "metrics_window": self._metrics.maxlen,
            "reports_dropped": s["reports_dropped"],
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
            "reports_dropped": 0,
        }
        self._metrics.clear()


# 全局单例
GLOBAL_HEADROOM = HeadroomEngine()