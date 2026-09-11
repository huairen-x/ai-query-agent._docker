"""
SQLite 数据库表结构定义

单一建表路径：所有表结构都在本文件的 _DDL 中定义，init_db() 负责执行与版本迁移。
运行时缓存表列名必须与 cache/sqlite_cache.py 写入的列一致（历史上这里存在两套
互相冲突的定义，导致 result_cache/semantic_cache 读写全部抛错且被静默吞掉）。

版本迁移策略：
    - 缓存三表是派生数据，版本不匹配时直接重建（DROP + CREATE）
    - sessions / audit_logs / feedback / learned_patterns 是历史数据，始终 IF NOT EXISTS 保留
"""
import os
import sqlite3
import threading

# 缓存表列定义变更时递增
SCHEMA_VERSION = 4

_CACHE_TABLES = ("semantic_cache", "metadata_cache", "result_cache")

# 历史表的新增列：老库通过 ALTER TABLE 原地补齐（IF NOT EXISTS 不会改已存在的表）
_ADDED_COLUMNS = (
    ("audit_logs", "trace_id", "TEXT DEFAULT ''"),
    ("audit_logs", "client", "TEXT DEFAULT ''"),
    ("audit_logs", "remote_addr", "TEXT DEFAULT ''"),
    ("audit_logs", "client_session", "TEXT DEFAULT ''"),
    ("audit_logs", "sql_text", "TEXT DEFAULT ''"),
    ("sessions", "trace_id", "TEXT DEFAULT ''"),
    ("sessions", "client_session", "TEXT DEFAULT ''"),
)

_DDL = """
CREATE TABLE IF NOT EXISTS semantic_cache (
    cache_key TEXT PRIMARY KEY,
    question TEXT,
    result TEXT,
    sql_text TEXT DEFAULT '',
    embedding TEXT,
    business_tags TEXT DEFAULT '[]',
    hit_count INTEGER DEFAULT 0,
    created_at REAL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_semantic_expires ON semantic_cache(expires_at);

CREATE TABLE IF NOT EXISTS metadata_cache (
    cache_key TEXT PRIMARY KEY,
    data TEXT,
    data_size INTEGER DEFAULT 0,
    hit_count INTEGER DEFAULT 0,
    created_at REAL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_metadata_expires ON metadata_cache(expires_at);

CREATE TABLE IF NOT EXISTS result_cache (
    cache_key TEXT PRIMARY KEY,
    sql_text TEXT,
    result TEXT,
    row_count INTEGER DEFAULT 0,
    hit_count INTEGER DEFAULT 0,
    created_at REAL,
    expires_at REAL
);
CREATE INDEX IF NOT EXISTS idx_result_expires ON result_cache(expires_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tenant_id TEXT DEFAULT '',
    question TEXT NOT NULL,
    workflow_step TEXT NOT NULL,
    node_name TEXT NOT NULL,
    input_data TEXT,
    output_data TEXT,
    token_estimate INTEGER DEFAULT 0,
    compression_ratio REAL DEFAULT 1.0,
    latency_ms REAL DEFAULT 0,
    cache_hit INTEGER DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    trace_id TEXT DEFAULT '',
    client_session TEXT DEFAULT '',
    sql_text TEXT DEFAULT '',
    client TEXT DEFAULT '',
    remote_addr TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT DEFAULT '',
    question TEXT NOT NULL,
    workflow_state TEXT,
    status TEXT DEFAULT 'running',
    cache_hits TEXT DEFAULT '{}',
    errors TEXT DEFAULT '[]',
    started_at REAL NOT NULL,
    completed_at REAL,
    created_at REAL NOT NULL,
    trace_id TEXT DEFAULT '',
    client_session TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    question TEXT NOT NULL,
    sql_text TEXT,
    rating INTEGER DEFAULT 0,
    correct INTEGER DEFAULT 0,
    feedback_text TEXT DEFAULT '',
    business_tags TEXT DEFAULT '[]',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learned_patterns (
    pattern_id TEXT PRIMARY KEY,
    keywords TEXT NOT NULL,
    sql_template TEXT NOT NULL,
    count INTEGER DEFAULT 1,
    correct_rate REAL DEFAULT 1.0,
    avg_rating REAL DEFAULT 5.0,
    last_seen REAL NOT NULL
);
"""

# 依赖新增列的索引：必须在 _migrate_columns() 补齐列之后才能建，
# 否则老库上 CREATE INDEX 会因列不存在而报错（IF NOT EXISTS 不检查列）。
_POST_MIGRATION_DDL = """
CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_audit_client_session ON audit_logs(client_session);
"""

_lock = threading.Lock()


def _data_dir() -> str:
    """数据目录：AGENT_DATA_DIR 优先，否则回退到仓库内 data/"""
    return os.environ.get("AGENT_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )


def get_db_path() -> str:
    return os.path.join(_data_dir(), "agent.db")


def get_connection() -> sqlite3.Connection:
    os.makedirs(_data_dir(), exist_ok=True)
    conn = sqlite3.connect(get_db_path(), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """为历史表补齐新增列（幂等）"""
    for table, column, declaration in _ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def init_db() -> None:
    """建表 + 版本迁移，幂等"""
    with _lock:
        conn = get_connection()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                # 缓存是派生数据：schema 变更时直接重建，避免列名不匹配导致静默失效
                for table in _CACHE_TABLES:
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.executescript(_DDL)
            _migrate_columns(conn)
            conn.executescript(_POST_MIGRATION_DDL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        finally:
            conn.close()

