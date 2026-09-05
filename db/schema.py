"""
SQLite 数据库表结构定义
"""
import os
import sqlite3
import threading

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "agent.db")
_lock = threading.Lock()


def get_db_path() -> str:
    return DB_PATH


def get_connection() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def create_all_tables(db_manager=None):
    """初始化数据库表结构，兼容带 db_manager 参数调用"""
    if db_manager:
        db_manager.execute("""
            CREATE TABLE IF NOT EXISTS semantic_cache (
                cache_key TEXT PRIMARY KEY, question TEXT, data TEXT, data_size INTEGER DEFAULT 0,
                hit_count INTEGER DEFAULT 0, created_at REAL, expires_at REAL
            )
        """)
        db_manager.execute("""
            CREATE TABLE IF NOT EXISTS metadata_cache (
                cache_key TEXT PRIMARY KEY, data TEXT, data_size INTEGER DEFAULT 0,
                hit_count INTEGER DEFAULT 0, created_at REAL, expires_at REAL
            )
        """)
        db_manager.execute("""
            CREATE TABLE IF NOT EXISTS result_cache (
                cache_key TEXT PRIMARY KEY, data TEXT, data_size INTEGER DEFAULT 0,
                hit_count INTEGER DEFAULT 0, created_at REAL, expires_at REAL
            )
        """)
        db_manager.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY, session_id TEXT, tenant_id TEXT DEFAULT '',
                question TEXT, workflow_step TEXT, node_name TEXT, input_data TEXT,
                output_data TEXT, token_estimate INTEGER DEFAULT 0,
                compression_ratio REAL DEFAULT 1.0, latency_ms REAL DEFAULT 0,
                cache_hit INTEGER DEFAULT 0, error TEXT, created_at REAL
            )
        """)
        db_manager.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, tenant_id TEXT DEFAULT '',
                question TEXT, workflow_state TEXT, status TEXT DEFAULT 'running',
                cache_hits TEXT DEFAULT '{}', errors TEXT DEFAULT '[]',
                started_at REAL, completed_at REAL, created_at REAL
            )
        """)
        db_manager.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY, session_id TEXT, question TEXT,
                sql_text TEXT, rating INTEGER DEFAULT 0, correct INTEGER DEFAULT 0,
                feedback_text TEXT DEFAULT '', business_tags TEXT DEFAULT '[]', created_at REAL
            )
        """)
        db_manager.execute("""
            CREATE TABLE IF NOT EXISTS learned_patterns (
                pattern_id TEXT PRIMARY KEY, keywords TEXT, sql_template TEXT,
                count INTEGER DEFAULT 1, correct_rate REAL DEFAULT 1.0,
                avg_rating REAL DEFAULT 5.0, last_seen REAL
            )
        """)
        return
    return init_db()


def init_db():
    """初始化所有表结构"""
    with _lock:
        conn = get_connection()
        try:
            _create_semantic_cache(conn)
            _create_metadata_cache(conn)
            _create_result_cache(conn)
            _create_audit_logs(conn)
            _create_sessions(conn)
            _create_feedback(conn)
            _create_patterns(conn)
            conn.commit()
        finally:
            conn.close()


def _create_semantic_cache(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS semantic_cache (
            cache_key TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            result TEXT NOT NULL,
            sql_text TEXT DEFAULT '',
            embedding TEXT,
            business_tags TEXT DEFAULT '[]',
            hit_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_expires ON semantic_cache(expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_question ON semantic_cache(question)")


def _create_metadata_cache(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata_cache (
            cache_key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            data_size INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metadata_expires ON metadata_cache(expires_at)")


def _create_result_cache(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS result_cache (
            cache_key TEXT PRIMARY KEY,
            sql_text TEXT NOT NULL,
            result TEXT NOT NULL,
            row_count INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_result_expires ON result_cache(expires_at)")


def _create_audit_logs(conn: sqlite3.Connection):
    conn.execute("""
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
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_logs(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id)")


def _create_sessions(conn: sqlite3.Connection):
    conn.execute("""
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
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")


def _create_feedback(conn: sqlite3.Connection):
    conn.execute("""
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
        )
    """)


def _create_patterns(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS learned_patterns (
            pattern_id TEXT PRIMARY KEY,
            keywords TEXT NOT NULL,
            sql_template TEXT NOT NULL,
            count INTEGER DEFAULT 1,
            correct_rate REAL DEFAULT 1.0,
            avg_rating REAL DEFAULT 5.0,
            last_seen REAL NOT NULL
        )
    """)