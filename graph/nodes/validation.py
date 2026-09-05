"""
Node 5: SQL 审查节点
安全校验 + 语法检查 + 性能评估
条件分支：验证失败可重试
"""
from __future__ import annotations
import time
from graph.state import AgentState

# SQL 白名单关键字
SAFE_KEYWORDS = {"select", "show", "describe", "explain", "with", "use"}
DANGEROUS_KEYWORDS = {"drop", "truncate", "delete", "insert", "update",
                      "alter", "create", "grant", "revoke", "exec"}

MAX_RETRY_ATTEMPTS = 2


def validation_node(state: AgentState) -> dict:
    """SQL 审查节点"""
    start = time.time()
    sql = state.get("sql", "")
    attempts = state.get("validation_attempts", 0)

    result = {
        "sql": sql,
        "valid": True,
        "safe": True,
        "warnings": [],
        "errors": [],
    }

    # 1. 安全检查
    safety = _check_safety(sql)
    result["safe"] = safety["safe"]
    result["warnings"].extend(safety["warnings"])

    if not safety["safe"]:
        result["errors"].append("SQL 安全校验未通过")

    # 2. 语法检查
    syntax = _check_syntax(sql)
    if not syntax["valid"]:
        result["valid"] = False
        result["errors"].append(syntax["error"])

    # 3. 性能评估
    performance = _assess_performance(sql)
    result["warnings"].extend(performance["warnings"])

    validation_passed = result["valid"] and result["safe"]

    return {
        "validation": result,
        "validation_passed": validation_passed,
        "validation_attempts": attempts + 1,
    }


def should_retry_sql(state: AgentState) -> str:
    """
    条件分支判断函数
    验证失败且未超过重试次数 → 返回 "retry" 重新生成 SQL
    验证通过或超过重试次数 → 返回 "pass"
    """
    validation_passed = state.get("validation_passed", False)
    attempts = state.get("validation_attempts", 0)
    errors = state.get("errors", [])

    if validation_passed:
        return "pass"

    if attempts < MAX_RETRY_ATTEMPTS:
        return "retry"

    # 超过重试次数，记录错误
    errors.append(f"SQL 审查未通过（已重试 {attempts} 次）")
    # 强制放行（避免死循环）
    return "force_pass"


def _check_safety(sql: str) -> dict:
    """SQL 安全检查"""
    result = {"safe": True, "warnings": []}
    sql_lower = sql.strip().lower()

    if not sql_lower:
        result["safe"] = False
        result["warnings"].append("SQL 为空")
        return result

    first_word = sql_lower.split()[0] if sql_lower.split() else ""
    if first_word not in SAFE_KEYWORDS:
        result["safe"] = False
        result["warnings"].append(f"SQL 首关键字 '{first_word}' 不在白名单中")

    for kw in DANGEROUS_KEYWORDS:
        if kw in sql_lower:
            result["safe"] = False
            result["warnings"].append(f"包含危险关键字 '{kw}'")

    if ";" in sql_lower.rstrip(";"):
        result["safe"] = False
        result["warnings"].append("SQL 包含多条语句")

    return result


def _check_syntax(sql: str) -> dict:
    """SQL 语法检查"""
    result = {"valid": True, "error": ""}
    sql_upper = sql.strip().upper()

    if not sql_upper:
        result["valid"] = False
        result["error"] = "SQL 为空"

    if not sql_upper.startswith("SELECT") and not sql_upper.startswith("WITH"):
        result["valid"] = False
        result["error"] = "SQL 必须以 SELECT 或 WITH 开头"

    if sql_upper.count("(") != sql_upper.count(")"):
        result["valid"] = False
        result["error"] = "括号不匹配"

    return result


def _assess_performance(sql: str) -> dict:
    """SQL 性能评估"""
    warnings = []
    sql_upper = sql.upper()

    if "SELECT" in sql_upper and "WHERE" not in sql_upper and "LIMIT" not in sql_upper:
        warnings.append("全表扫描：缺少 WHERE 或 LIMIT 条件")

    if "SELECT *" in sql_upper:
        warnings.append("避免 SELECT *，建议显式指定字段")

    join_count = sql_upper.count("JOIN")
    if join_count > 3:
        warnings.append(f"JOIN 数量过多 ({join_count})，可能影响性能")

    if sql_upper.count("SELECT") > 2:
        warnings.append("包含子查询，考虑使用临时表或 CTE 优化")

    return {"warnings": warnings}