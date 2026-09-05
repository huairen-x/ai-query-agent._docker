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


# ============================================================
# 审计配置
# ============================================================
@dataclass
class AuditConfig:
    enabled: bool = True
    db_path: str = "data/audit.db"
    retention_days: int = 90


# ============================================================
# 模板引擎配置
# ============================================================
@dataclass
class TemplateConfig:
    enabled: bool = True
    template_path: str = "templates/templates.yaml"
    auto_learn: bool = True
    min_confidence: float = 0.6


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

        # LangGraph
        c.langgraph.checkpoint_enabled = os.environ.get("LANGGRAPH_CHECKPOINT", "true").lower() == "true"

        return c


# 全局单例
GLOBAL_CONFIG = AppConfig.from_env()