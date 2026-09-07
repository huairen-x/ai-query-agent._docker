"""
SQL 校验节点 - SQL 注入检测 + 语法校验 + 安全重试
使用 token 级 SQL 注入检测，避免子串匹配误报
"""
from __future__ import annotations
import re
import sqlparse
from typing import Any
from observability import trace_node, sql_validation_blocked

# SQL 关键字分类
DDL_KEYWORDS = {"alter", "create", "drop", "truncate", "rename", "replace"}
DML_DANGEROUS = {"delete", "update", "insert", "load", "merge", "call"}
EXEC_KEYWORDS = {"exec", "execute", "sp_executesql", "xp_cmdshell", "shell", "exec_at"}
PRIVILEGE_KEYWORDS = {"grant", "revoke", "deny"}
TRANSACTION_KEYWORDS = {"commit", "rollback", "savepoint", "begin"}
ALL_DANGEROUS = DDL_KEYWORDS | DML_DANGEROUS | EXEC_KEYWORDS | PRIVILEGE_KEYWORDS

# 注释模式（用于剥离后检测）
COMMENT_PATTERNS = [
    re.compile(r"--.*$", re.MULTILINE),           # 单行注释
    re.compile(r"/\*.*?\*/", re.DOTALL),           # 多行注释
    re.compile(r"#.*$", re.MULTILINE),             # MySQL 单行注释
]


def strip_comments(sql: str) -> str:
    """剥离 SQL 中的注释"""
    cleaned = sql
    for pattern in COMMENT_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned


def extract_tokens(sql: str) -> set[str]:
    """
    提取 SQL 中的有效 token（只保留完整词，过滤字符串字面量和数字）
    使用 sqlparse 做词法分析，避免子串匹配
    """
    cleaned = strip_comments(sql)
    tokens = set()
    parsed = sqlparse.parse(cleaned)
    for statement in parsed:
        for token in statement.flatten():
            ttype = token.ttype
            value = token.value.lower().strip()
            # 只检查关键字类型的 token
            if ttype is None or not value:
                continue
            ttype_str = str(ttype)
            if "Keyword" in ttype_str and value:
                tokens.add(value)
    return tokens


def check_sql_injection(sql: str) -> tuple[bool, str | None]:
    """
    检测 SQL 注入风险
    返回: (is_safe, error_message)

    检测策略:
    1. 使用 sqlparse 做词法分析，只检查关键字类型的 token
    2. 检测联合查询注入 (UNION ... SELECT)
    3. 检测堆叠查询 (; DROP, ; DELETE 等)
    """
    if not sql or not sql.strip():
        return False, "SQL 语句为空"

    # 检测堆叠查询（多条语句）
    statements = sqlparse.parse(sql)
    if len(statements) > 1:
        return False, "检测到多条 SQL 语句（堆叠查询）"

    # 提取关键字 token 并检查危险操作
    tokens = extract_tokens(sql)
    dangerous_found = tokens & ALL_DANGEROUS

    if dangerous_found:
        # 提取具体哪些危险关键字被命中
        matched = ", ".join(sorted(dangerous_found))
        return False, f"检测到禁止的 SQL 操作: {matched}"

    # 检测 UNION SELECT（数据泄露）
    cleaned = strip_comments(sql).lower()
    if re.search(r'\bunion\b.*\bselect\b', cleaned, re.DOTALL):
        return False, "检测到 UNION SELECT 操作（数据泄露风险）"

    return True, None


def validate_sql(sql: str) -> tuple[bool, str | None]:
    """
    综合 SQL 校验
    返回: (is_valid, error_message)
    """
    # 1. 注入检测
    safe, error = check_sql_injection(sql)
    if not safe:
        return False, error

    # 2. 基本语法检查 - 必须有 SELECT
    cleaned = strip_comments(sql).strip().lower()
    if not cleaned.startswith("select"):
        return False, "只允许 SELECT 查询语句"

    # 3. 检查是否以 ; 结尾，多条语句风险
    if cleaned.count(";") > 1:
        return False, "检测到多条 SQL 语句"

    return True, None


def should_retry_sql(result: dict[str, Any], retry_count: int, max_retries: int = 3) -> str:
    """
    判断是否需要重试 SQL 执行
    返回: "retry" / "pass" / "force_pass"
    """
    if retry_count >= max_retries:
        return "force_pass"

    error = result.get("error", "")
    if not error:
        return "pass"

    error_lower = error.lower()

    # 可重试的错误类型
    retryable_errors = [
        "timeout", "time out", "deadlock", "lock wait",
        "connection", "network", "retry", "too many",
        "temporary", "transient", "throttl",
    ]

    for keyword in retryable_errors:
        if keyword in error_lower:
            return "retry"

    return "pass"


@trace_node("validation")
def validation_node(state: dict) -> dict:
    """LangGraph 校验节点 - 对生成的 SQL 进行安全性和语法校验"""
    sql = state.get("sql", "")
    if not sql:
        return {**state, "validation_passed": False, "validation_error": "无 SQL 需要校验"}

    safe, inj_error = check_sql_injection(sql)
    valid, val_error = validate_sql(sql)

    if not safe or not valid:
        sql_validation_blocked.inc()
        errors = [e for e in [inj_error, val_error] if e]
        return {
            **state,
            "validation_passed": False,
            "validation_error": "; ".join(errors),
            "errors": state.get("errors", []) + errors,
        }

    return {
        **state,
        "validation_passed": True,
        "validation_error": None,
    }