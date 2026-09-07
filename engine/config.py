"""
全局配置 - 集中管理所有模块配置
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# Headroom 压缩配置
# ============================================================
@dataclass
class HeadroomConfig:
    enabled: bool = True
    default_target_ratio: float = 0.3
    min_compression_ratio: float = 0.1
    quality_score_threshold: float = 0.85
    monitor_enabled: bool = True
    strategy_ratios: dict = field(default_factory=lambda: {
        "conversation_history": 0.15,
        "metadata": 0.25,
        "system_prompt": 0.35,
        "sql_result": 0.10,
        "sql_code": 0.30,
        "glossary": 0.25,
    })


# ============================================================
# 缓存配置 (SQLite 持久化)
# ============================================================
@dataclass
class CacheConfig:
    semantic_cache_enabled: bool = True
    semantic_cache_ttl: int = 300
    semantic_cache_threshold: float = 0.95
    semantic_cache_business_threshold: float = 0.92
    metadata_cache_enabled: bool = True
    metadata_cache_ttl: int = 300
    result_cache_enabled: bool = True
    result_cache_ttl: int = 60
    db_path: str = "data/cache.db"


# ============================================================
# LangGraph 工作流配置
# ============================================================
@dataclass
class LangGraphConfig:
    max_retries: int = 3
    step_timeout: int = 60
    workflow_timeout: int = 300
    enable_parallel: bool = True
    enable_conditional_branches: bool = True
    # SQLite 持久化（替代 Redis）
    persistence_db_path: str = "data/workflow_state.db"
    checkpoint_enabled: bool = True


# ============================================================
# 上下文清理配置
# ============================================================
@dataclass
class ContextCleanupConfig:
    enabled: bool = True
    max_context_size: int = 10000
    dedup_enabled: bool = True
    empty_filter_enabled: bool = True
    blocklist_enabled: bool = True
    blocklist_patterns: list = field(default_factory=lambda: [
        "api_key", "password", "secret", "token", "authorization"
    ])
    # Pipeline 三层上下文处理
    threshold_a_from_budget: bool = True  # True=从 TokenBudget 动态获取, False=用 fixed 值
    threshold_a_fixed: int = 3000         # L1 目标阈值（固定值，不动态时用）
    threshold_b_fixed: int = 1500         # L2 目标阈值（固定值）
    l3_enabled: bool = False              # L3 LLM 摘要功能（默认关闭）
    l3_max_tokens: int = 500              # L3 摘要最大 token 数


# ============================================================
# 审计配置
# ============================================================
@dataclass
class AuditConfig:
    enabled: bool = True
    db_path: str = "data/audit.db"
    retention_days: int = 90


# ============================================================
# SQL 模板配置
# ============================================================
@dataclass
class TemplateConfig:
    enabled: bool = True
    template_path: str = "templates/templates.yaml"
    auto_learn: bool = True
    min_confidence: float = 0.5


# ============================================================
# 可观测性配置
# ============================================================
@dataclass
class ObservabilityConfig:
    metrics_enabled: bool = True
    log_level: str = "INFO"
    log_format: str = "json"  # json or text


# ============================================================
# 弹性配置
# ============================================================
@dataclass
class ResilienceConfig:
    circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: float = 30.0

    retry_enabled: bool = True
    retry_max_attempts: int = 3
    retry_base_delay: float = 0.1
    retry_max_delay: float = 5.0

    timeout_enabled: bool = True
    workflow_timeout: float = 60.0
    node_timeout: float = 30.0
    sql_timeout: float = 10.0

    degradation_enabled: bool = True


# ============================================================
# 安全配置
# ============================================================
@dataclass
class RateLimitConfig:
    enabled: bool = True
    global_rps: float = 100.0
    per_ip_rps: float = 10.0
    burst_size: int = 20


@dataclass
class CorsConfig:
    allowed_origins: list = field(default_factory=lambda: ["*"])
    allow_credentials: bool = True
    allow_methods: list = field(default_factory=lambda: ["GET", "POST", "OPTIONS"])
    allow_headers: list = field(default_factory=lambda: ["Content-Type", "Authorization", "X-Request-ID"])


@dataclass
class SecurityConfig:
    enabled: bool = True
    api_keys: list = field(default_factory=list)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    cors: CorsConfig = field(default_factory=CorsConfig)


# ============================================================
# 全局配置
# ============================================================
@dataclass
class AppConfig:
    headroom: HeadroomConfig = field(default_factory=HeadroomConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    langgraph: LangGraphConfig = field(default_factory=LangGraphConfig)
    context_cleanup: ContextCleanupConfig = field(default_factory=ContextCleanupConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    template: TemplateConfig = field(default_factory=TemplateConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    resilience: ResilienceConfig = field(default_factory=ResilienceConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    mock_mode: bool = True
    debug: bool = False
    tenant_id: str = "default"

    @classmethod
    def from_env(cls) -> "AppConfig":
        c = cls()
        c.mock_mode = os.environ.get("MOCK_MODE", "true").lower() == "true"
        c.debug = os.environ.get("DEBUG", "false").lower() == "true"
        c.tenant_id = os.environ.get("TENANT_ID", "default")

        # Headroom
        c.headroom.enabled = os.environ.get("HEADROOM_ENABLED", "true").lower() == "true"
        ratio = os.environ.get("HEADROOM_TARGET_RATIO")
        if ratio:
            c.headroom.default_target_ratio = float(ratio)

        # Cache
        ttl = os.environ.get("CACHE_TTL")
        if ttl:
            c.cache.semantic_cache_ttl = int(ttl)
            c.cache.metadata_cache_ttl = int(ttl)
            c.cache.result_cache_ttl = int(ttl)

        # Security
        c.security.enabled = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
        keys_str = os.environ.get("API_KEYS", "")
        if keys_str:
            c.security.api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        c.security.rate_limit.enabled = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() == "true"
        rps = os.environ.get("RATE_LIMIT_GLOBAL_RPS")
        if rps:
            c.security.rate_limit.global_rps = float(rps)
        cors_origins = os.environ.get("CORS_ORIGINS", "")
        if cors_origins:
            c.security.cors.allowed_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

        # Resilience
        c.resilience.circuit_breaker_enabled = os.environ.get("CIRCUIT_BREAKER_ENABLED", "true").lower() == "true"
        cb_threshold = os.environ.get("CIRCUIT_BREAKER_THRESHOLD")
        if cb_threshold:
            c.resilience.circuit_breaker_failure_threshold = int(cb_threshold)
        cb_timeout = os.environ.get("CIRCUIT_BREAKER_TIMEOUT")
        if cb_timeout:
            c.resilience.circuit_breaker_recovery_timeout = float(cb_timeout)
        c.resilience.retry_enabled = os.environ.get("RETRY_ENABLED", "true").lower() == "true"
        retry_attempts = os.environ.get("RETRY_MAX_ATTEMPTS")
        if retry_attempts:
            c.resilience.retry_max_attempts = int(retry_attempts)
        c.resilience.timeout_enabled = os.environ.get("TIMEOUT_ENABLED", "true").lower() == "true"
        wf_timeout = os.environ.get("WORKFLOW_TIMEOUT")
        if wf_timeout:
            c.resilience.workflow_timeout = float(wf_timeout)
        sql_timeout = os.environ.get("SQL_TIMEOUT")
        if sql_timeout:
            c.resilience.sql_timeout = float(sql_timeout)
        c.resilience.degradation_enabled = os.environ.get("DEGRADATION_ENABLED", "true").lower() == "true"

        # Template
        template_path = os.environ.get("TEMPLATE_PATH")
        if template_path:
            c.template.template_path = template_path
        c.template.enabled = os.environ.get("TEMPLATE_ENABLED", "true").lower() == "true"

        # Observability
        c.observability.metrics_enabled = os.environ.get("METRICS_ENABLED", "true").lower() == "true"
        log_level = os.environ.get("LOG_LEVEL")
        if log_level:
            c.observability.log_level = log_level.upper()

        # LangGraph
        c.langgraph.checkpoint_enabled = os.environ.get("LANGGRAPH_CHECKPOINT", "true").lower() == "true"

        # Pipeline 三层上下文处理
        c.context_cleanup.threshold_a_from_budget = os.environ.get("THRESHOLD_A_FROM_BUDGET", "true").lower() == "true"
        ta_fixed = os.environ.get("THRESHOLD_A_FIXED")
        if ta_fixed:
            c.context_cleanup.threshold_a_fixed = int(ta_fixed)
        tb_fixed = os.environ.get("THRESHOLD_B_FIXED")
        if tb_fixed:
            c.context_cleanup.threshold_b_fixed = int(tb_fixed)
        c.context_cleanup.l3_enabled = os.environ.get("L3_ENABLED", "false").lower() == "true"
        l3_max = os.environ.get("L3_MAX_TOKENS")
        if l3_max:
            c.context_cleanup.l3_max_tokens = int(l3_max)

        return c


# 全局单例
GLOBAL_CONFIG = AppConfig.from_env()