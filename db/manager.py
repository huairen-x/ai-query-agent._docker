"""
SQLite 数据库连接管理器
提供线程安全的连接池管理
"""
import sqlite3
import threading
import time
import json
from contextlib import contextmanager
from db.schema import get_db_path, get_connection


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

    def get_conn(self) -> sqlite3.Connection:
        """获取当前线程的连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = get_connection()
        return self._local.conn

    def close(self):
        """关闭当前线程的连接"""
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    @contextmanager
    def transaction(self):
        """事务上下文"""
        conn = self.get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = None, retries: int = 3) -> sqlite3.Cursor:
        """执行 SQL（带自动重试）"""
        last_error = None
        for attempt in range(retries):
            try:
                conn = self.get_conn()
                cursor = conn.execute(sql, params or ())
                with self._lock:
                    self._stats["queries"] += 1
                return cursor
            except sqlite3.OperationalError as e:
                last_error = e
                if "locked" in str(e).lower() and attempt < retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                with self._lock:
                    self._stats["errors"] += 1
                raise
            except Exception as e:
                with self._lock:
                    self._stats["errors"] += 1
                raise

    def executemany(self, sql: str, params_list: list):
        """批量执行"""
        conn = self.get_conn()
        conn.executemany(sql, params_list)
        conn.commit()

    def fetch_one(self, sql: str, params: tuple = None) -> dict:
        """查单条"""
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetch_all(self, sql: str, params: tuple = None) -> list[dict]:
        """查多条"""
        cursor = self.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def insert(self, table: str, data: dict) -> bool:
        """插入记录"""
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
        self.execute(sql, tuple(data.values()))
        return True

    def delete_expired(self, table: str) -> int:
        """删除过期记录"""
        now = time.time()
        cursor = self.execute(f"DELETE FROM {table} WHERE expires_at < ?", (now,))
        return cursor.rowcount

    def cleanup(self):
        """清理过期缓存"""
        total = 0
        for table in ["semantic_cache", "metadata_cache", "result_cache"]:
            total += self.delete_expired(table)
        return total

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)


GLOBAL_DB_MANAGER = DatabaseManager()