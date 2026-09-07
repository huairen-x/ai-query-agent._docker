"""
Prometheus Metrics - 零依赖实现
支持 Counter / Histogram / Gauge，输出 Prometheus 文本格式
"""
from __future__ import annotations
import time
import threading
from collections import defaultdict


class Counter:
    """计数器 - 只增不减"""

    def __init__(self, name: str, help: str, labels: list[str] = None):
        self.name = name
        self.help = help
        self.labels = labels or []
        self._values: dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **label_values):
        with self._lock:
            key = tuple(label_values.get(k, "") for k in self.labels)
            self._values[key] += value

    def collect(self) -> list[str]:
        lines = []
        lines.append(f"# HELP {self.name} {self.help}")
        lines.append(f"# TYPE {self.name} counter")
        if not self._values:
            lines.append(f"{self.name} 0")
        else:
            for key, val in sorted(self._values.items()):
                if self.labels:
                    labels = ",".join(f'{k}="{v}"' for k, v in zip(self.labels, key))
                    lines.append(f'{self.name}{{{labels}}} {val}')
                else:
                    lines.append(f"{self.name} {val}")
        return lines


class Gauge:
    """仪表盘 - 可增可减"""

    def __init__(self, name: str, help: str, labels: list[str] = None):
        self.name = name
        self.help = help
        self.labels = labels or []
        self._values: dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def set(self, value: float, **label_values):
        with self._lock:
            key = tuple(label_values.get(k, "") for k in self.labels)
            self._values[key] = value

    def inc(self, value: float = 1.0, **label_values):
        with self._lock:
            key = tuple(label_values.get(k, "") for k in self.labels)
            self._values[key] += value

    def dec(self, value: float = 1.0, **label_values):
        with self._lock:
            key = tuple(label_values.get(k, "") for k in self.labels)
            self._values[key] -= value

    def collect(self) -> list[str]:
        lines = []
        lines.append(f"# HELP {self.name} {self.help}")
        lines.append(f"# TYPE {self.name} gauge")
        if not self._values:
            lines.append(f"{self.name} 0")
        else:
            for key, val in sorted(self._values.items()):
                if self.labels:
                    labels = ",".join(f'{k}="{v}"' for k, v in zip(self.labels, key))
                    lines.append(f'{self.name}{{{labels}}} {val}')
                else:
                    lines.append(f"{self.name} {val}")
        return lines


class Histogram:
    """直方图 - 记录延迟分布"""

    def __init__(self, name: str, help: str, labels: list[str] = None,
                 buckets: list[float] = None):
        self.name = name
        self.help = help
        self.labels = labels or []
        self.buckets = buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        self._counts: dict[tuple, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self._sums: dict[tuple, float] = defaultdict(float)
        self._totals: dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def observe(self, value: float, **label_values):
        with self._lock:
            key = tuple(label_values.get(k, "") for k in self.labels)
            self._totals[key] += 1
            self._sums[key] += value
            for b in self.buckets:
                if value <= b:
                    self._counts[key][b] += 1

    def collect(self) -> list[str]:
        lines = []
        lines.append(f"# HELP {self.name} {self.help}")
        lines.append(f"# TYPE {self.name} histogram")
        if not self._totals:
            lines.append(f'{self.name}_bucket{{le="+Inf"}} 0')
            lines.append(f"{self.name}_sum 0")
            lines.append(f"{self.name}_count 0")
        else:
            for key in sorted(self._totals.keys()):
                labels_str = ",".join(f'{k}="{v}"' for k, v in zip(self.labels, key))
                prefix = f'{self.name}{{{labels_str},' if self.labels else f'{self.name}{{'
                for b in self.buckets:
                    count = int(self._counts[key][b])
                    lines.append(f'{prefix}le="{b}"}} {count}')
                lines.append(f'{prefix}le="+Inf"}} {int(self._totals[key])}')
                lines.append(f'{self.name}_sum{{{labels_str}}} {self._sums[key]}' if self.labels
                             else f'{self.name}_sum {self._sums[key]}')
                lines.append(f'{self.name}_count{{{labels_str}}} {int(self._totals[key])}' if self.labels
                             else f'{self.name}_count {int(self._totals[key])}')
        return lines


class MetricsRegistry:
    """全局 Metrics 注册表"""

    def __init__(self):
        self._metrics: dict[str, Counter | Histogram | Gauge] = {}
        self._lock = threading.Lock()

    def register(self, metric: Counter | Histogram | Gauge):
        with self._lock:
            if metric.name in self._metrics:
                return
            self._metrics[metric.name] = metric

    def counter(self, name: str, help: str, labels: list[str] = None) -> Counter:
        return self._get_or_create(name, help, labels, Counter)

    def gauge(self, name: str, help: str, labels: list[str] = None) -> Gauge:
        return self._get_or_create(name, help, labels, Gauge)

    def histogram(self, name: str, help: str, labels: list[str] = None,
                  buckets: list[float] = None) -> Histogram:
        return self._get_or_create(name, help, labels, Histogram, buckets=buckets)

    def _get_or_create(self, name, help, labels, cls, **kwargs):
        with self._lock:
            if name in self._metrics:
                return self._metrics[name]
            metric = cls(name=name, help=help, labels=labels, **kwargs)
            self._metrics[name] = metric
            return metric

    def collect_all(self) -> str:
        lines = []
        with self._lock:
            for metric in sorted(self._metrics.values(), key=lambda m: m.name):
                lines.extend(metric.collect())
        return "\n".join(lines) + "\n"


# 全局单例
GLOBAL_METRICS = MetricsRegistry()

# ============================================================
# 预定义指标
# ============================================================

http_requests_total = GLOBAL_METRICS.counter("http_requests_total", "HTTP 请求总数", ["method", "endpoint", "status"])
http_request_duration_ms = GLOBAL_METRICS.histogram("http_request_duration_ms", "HTTP 请求延迟 (ms)", ["method", "endpoint"],
                                                     buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000])
concurrent_requests = GLOBAL_METRICS.gauge("concurrent_requests", "当前并发请求数")

workflow_node_duration_ms = GLOBAL_METRICS.histogram("workflow_node_duration_ms", "工作流节点执行延迟 (ms)", ["node", "status"],
                                                      buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000])
workflow_total = GLOBAL_METRICS.counter("workflow_total", "工作流执行次数", ["status"])

cache_hits_total = GLOBAL_METRICS.counter("cache_hits_total", "缓存命中次数", ["cache_type"])
cache_misses_total = GLOBAL_METRICS.counter("cache_misses_total", "缓存未命中次数", ["cache_type"])
cache_size = GLOBAL_METRICS.gauge("cache_size", "缓存条目数", ["cache_type"])

sql_execution_duration_ms = GLOBAL_METRICS.histogram("sql_execution_duration_ms", "SQL 执行延迟 (ms)", ["status"],
                                                      buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000])
sql_validation_blocked = GLOBAL_METRICS.counter("sql_validation_blocked", "SQL 校验拦截次数")

# 弹性指标
circuit_breaker_state = GLOBAL_METRICS.gauge("circuit_breaker_state", "熔断器状态 (0=CLOSED 1=HALF_OPEN 2=OPEN)", ["name"])
retry_attempts_total = GLOBAL_METRICS.counter("retry_attempts_total", "重试次数", ["name"])


# ============================================================
# 注册弹性模块回调
# ============================================================
def _register_resilience_callbacks():
    """注册熔断器和重试的 metrics 回调"""
    try:
        from resilience.circuit_breaker import register_state_reporter, CircuitState

        def _report_cb_state(name: str, state: CircuitState):
            circuit_breaker_state.set(int(state), name=name)

        register_state_reporter(_report_cb_state)

        from resilience.retry import register_retry_reporter

        def _report_retry(name: str, attempt: int):
            retry_attempts_total.inc(name=name)

        register_retry_reporter(_report_retry)
    except ImportError:
        pass  # resilience 模块未加载时跳过


_register_resilience_callbacks()