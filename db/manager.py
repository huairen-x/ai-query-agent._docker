"""
SQLite 数据库连接管理器
提供线程安全的连接池管理

提交语义（曾经这里是全系统最隐蔽的 bug）：
    旧实现里 execute()/insert() 从不 commit，写操作只在连接关闭时才落盘。
    线程本地长连接持有未提交写事务 → 其他连接写入一律 "database is locked"，
    审计日志与缓存因此静默全部失效。现在除显式 transaction() 内部外，
    每条语句执行后立即提交。
"""
import sqlite3
import threading
import time
from contextlib import contextmanager
from db.schema import get_connection


class DatabaseManager:
    """
    SQLite 数据库管理器
    - 线程安全连接
    - 自动重试
    - 统计追踪
    """

    def __init__(self):
        self._local = threading.local()
        self._lock = threading.RLock()
        self._stats = {
            "queries": 0,
            "errors": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    # ── 连接 ──────────────────────────────────────────────
    def get_conn(self) -> sqlite3.Connection:
        """获取当前线程的连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = get_connection()
        return self._local.conn

    def close(self):
        """关闭当前线程的连接"""
        if getattr(self._local, "conn", None):
            try:
                self._local.conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    @contextmanager
    def transaction(self):
        """事务上下文：期间 execute() 不自动提交，由这里统一提交/回滚"""
        conn = self.get_conn()
        self._local.in_tx = True
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._local.in_tx = False

    # ── 执行 ──────────────────────────────────────────────
    def execute(self, sql: str, params: tuple = None, retries: int = 3) -> sqlite3.Cursor:
        """执行 SQL（带锁重试）；显式事务外自动提交"""
        last_error = None
        for attempt in range(retries):
            try:
                conn = self.get_conn()
                cursor = conn.execute(sql, params or ())
                if not getattr(self._local, "in_tx", False):
                    conn.commit()
                with self._lock:
                    self._stats["queries"] += 1
                return cursor
            except sqlite3.OperationalError as e:
                last_error = e
                message = str(e).lower()
                retryable = "locked" in message or "busy" in message
                if retryable and attempt < retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                with self._lock:
                    self._stats["errors"] += 1
                raise
            except Exception:
                with self._lock:
                    self._stats["errors"] += 1
                raise
        raise last_error

    def executemany(self, sql: str, params_list: list) -> None:
        """批量执行"""
        conn = self.get_conn()
        conn.executemany(sql, params_list)
        if not getattr(self._local, "in_tx", False):
            conn.commit()

    # ── 查询 ──────────────────────────────────────────────
    def fetch_one(self, sql: str, params: tuple = None) -> dict:
        """查单条"""
        row = self.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql: str, params: tuple = None) -> list[dict]:
        """查多条"""
        return [dict(row) for row in self.execute(sql, params).fetchall()]

    def count(self, table: str, where: str = "", params: tuple = None) -> int:
        """计数"""
        sql = f"SELECT COUNT(*) AS cnt FROM {table}"
        if where:
            sql += f" WHERE {where}"
        row = self.fetch_one(sql, params)
        return int(row["cnt"]) if row else 0

    # ── 写入 ──────────────────────────────────────────────
    def insert(self, table: str, data: dict) -> bool:
        """插入记录（UPSERT）"""
        if not data:
            raise ValueError("insert 需要至少一个字段")
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        self.execute(
            f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        return True

    def delete_expired(self, table: str) -> int:
        """删除过期记录"""
        cursor = self.execute(f"DELETE FROM {table} WHERE expires_at < ?", (time.time(),))
        return cursor.rowcount

    def cleanup(self) -> int:
        """清理过期缓存"""
        total = 0
        for table in ("semantic_cache", "metadata_cache", "result_cache"):
            total += self.delete_expired(table)
        return total

    def purge_older_than(self, table: str, column: str, retention_days: int) -> int:
        """删除表中 column 早于保留期的行，返回删除行数"""
        cutoff = time.time() - retention_days * 86400
        cursor = self.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))
        return cursor.rowcount

    def purge_audit(self, retention_days: int) -> int:
        """按保留期回收审计日志与会话"""
        return sum(
            self.purge_older_than(table, "created_at", retention_days)
            for table in ("audit_logs", "sessions")
        )

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)


GLOBAL_DB_MANAGER = DatabaseManager()
