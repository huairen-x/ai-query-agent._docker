"""
SQL 模板引擎 - 从历史查询中自动学习并匹配 SQL 模板
支持 YAML 模板定义、变量替换、自动学习
"""
from __future__ import annotations
import re
import yaml
import json
import os
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from engine.config import GLOBAL_CONFIG
from db.manager import GLOBAL_DB_MANAGER


@dataclass
class SQLTemplate:
    """SQL 模板"""
    name: str
    pattern: str
    sql_template: str
    description: str = ""
    variables: list[str] = field(default_factory=list)
    confidence: float = 1.0
    hit_count: int = 0
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0


class TemplateEngine:
    """
    SQL 模板引擎
    - 从 YAML 文件加载预定义模板
    - 自动从历史查询中学习新模板
    - 语义匹配相似查询到模板
    - 变量替换生成 SQL
    """

    def __init__(self):
        config = GLOBAL_CONFIG.template
        self.enabled = config.enabled
        self.template_path = config.template_path
        self.auto_learn = config.auto_learn
        self.min_confidence = config.min_confidence

        self._templates: dict[str, SQLTemplate] = {}
        self._lock = threading.RLock()
        self._stats = {"hits": 0, "misses": 0, "learned": 0}

        self._load_templates()
        self._ensure_templates_table()
        self._load_from_db()

    def _ensure_templates_table(self):
        try:
            GLOBAL_DB_MANAGER.execute("""
                CREATE TABLE IF NOT EXISTS learned_templates (
                    name TEXT PRIMARY KEY,
                    pattern TEXT NOT NULL DEFAULT '',
                    sql_template TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    variables TEXT DEFAULT '[]',
                    tags TEXT DEFAULT '[]',
                    confidence REAL DEFAULT 0.7,
                    hit_count INTEGER DEFAULT 0,
                    created_at REAL NOT NULL
                )
            """)
        except Exception:
            pass

    def _load_from_db(self):
        try:
            rows = GLOBAL_DB_MANAGER.fetch_all("SELECT * FROM learned_templates")
            for row in rows:
                template = SQLTemplate(
                    name=row["name"],
                    pattern=row["pattern"],
                    sql_template=row["sql_template"],
                    description=row["description"],
                    variables=json.loads(row["variables"]),
                    tags=json.loads(row["tags"]),
                    confidence=row["confidence"],
                    hit_count=row["hit_count"],
                    created_at=row["created_at"],
                )
                if template.name not in self._templates:
                    self._templates[template.name] = template
        except Exception:
            pass

    def _save_to_db(self, template: SQLTemplate):
        try:
            GLOBAL_DB_MANAGER.insert("learned_templates", {
                "name": template.name,
                "pattern": template.pattern,
                "sql_template": template.sql_template,
                "description": template.description,
                "variables": json.dumps(template.variables, ensure_ascii=False),
                "tags": json.dumps(template.tags, ensure_ascii=False),
                "confidence": template.confidence,
                "hit_count": template.hit_count,
                "created_at": template.created_at or __import__("time").time(),
            })
        except Exception:
            pass

    def _load_templates(self):
        """从 YAML 加载预定义模板"""
        if not os.path.exists(self.template_path):
            return

        try:
            with open(self.template_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            templates = data.get("templates", [])
            for t in templates:
                template = SQLTemplate(
                    name=t["name"],
                    pattern=t.get("pattern", ""),
                    sql_template=t["sql"],
                    description=t.get("description", ""),
                    variables=t.get("variables", []),
                    tags=t.get("tags", []),
                )
                self._templates[t["name"]] = template
        except (yaml.YAMLError, KeyError, OSError):
            pass

    def _extract_variables(self, sql: str) -> dict[str, str]:
        """从 SQL 中提取变量占位符"""
        vars = {}
        pattern = r'\{\{(\w+)\}\}'
        for match in re.finditer(pattern, sql):
            vars[match.group(1)] = ""
        return vars

    def _generalize_sql(self, sql: str) -> tuple[str, dict]:
        """将具体 SQL 泛化为模板模式"""
        generalized = sql
        variables = {}

        # 替换字符串字面量
        str_pattern = r"'[^']*'"
        str_idx = 0
        def replace_str(m):
            nonlocal str_idx
            var_name = f"str_val_{str_idx}"
            str_idx += 1
            variables[var_name] = m.group(0)
            return f"{{{{{var_name}}}}}"
        generalized = re.sub(str_pattern, replace_str, generalized)

        # 替换数字字面量
        num_pattern = r'\b(\d+)\b'
        num_idx = 0
        def replace_num(m):
            nonlocal num_idx
            var_name = f"num_val_{num_idx}"
            num_idx += 1
            variables[var_name] = m.group(0)
            return f"{{{{{var_name}}}}}"
        generalized = re.sub(num_pattern, replace_num, generalized)

        return generalized, variables

    def _compute_similarity(self, sql_a: str, sql_b: str) -> float:
        """计算两条 SQL 的相似度"""
        # 归一化
        a = re.sub(r'\s+', ' ', sql_a.strip()).lower()
        b = re.sub(r'\s+', ' ', sql_b.strip()).lower()

        if a == b:
            return 1.0

        # Token 级别 Jaccard 相似度
        tokens_a = set(re.findall(r'\w+', a))
        tokens_b = set(re.findall(r'\w+', b))

        if not tokens_a or not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        return len(intersection) / len(union)

    def match(self, question: str, sql: str = None) -> Optional[SQLTemplate]:
        """
        匹配最佳模板

        Args:
            question: 用户自然语言问题
            sql: 可选，已生成的 SQL

        Returns:
            匹配到的模板或 None
        """
        if not self.enabled:
            return None

        with self._lock:
            best_score = 0.0
            best_template = None

            for template in self._templates.values():
                score = 0.0

                # 关键词匹配
                if template.pattern:
                    pattern_keywords = set(re.findall(r'\w+', template.pattern.lower()))
                    question_keywords = set(re.findall(r'\w+', question.lower()))
                    if pattern_keywords:
                        kw_score = len(pattern_keywords & question_keywords) / len(pattern_keywords)
                        score += kw_score * 0.6

                # SQL 结构匹配
                if sql and template.sql_template:
                    sql_score = self._compute_similarity(sql, template.sql_template)
                    score += sql_score * 0.4

                if score > best_score and score >= self.min_confidence:
                    best_score = score
                    best_template = template

            if best_template:
                best_template.hit_count += 1
                self._stats["hits"] += 1
                try:
                    GLOBAL_DB_MANAGER.execute(
                        "UPDATE learned_templates SET hit_count = ? WHERE name = ?",
                        (best_template.hit_count, best_template.name)
                    )
                except Exception:
                    pass
            else:
                self._stats["misses"] += 1

            return best_template

    def render(self, template_name: str, variables: dict) -> Optional[str]:
        """
        渲染模板生成 SQL

        Args:
            template_name: 模板名称
            variables: 变量值字典

        Returns:
            生成的 SQL 或 None
        """
        template = self._templates.get(template_name)
        if not template:
            return None

        sql = template.sql_template
        for var_name, var_value in variables.items():
            placeholder = f"{{{{{var_name}}}}}"
            if placeholder in sql:
                sql = sql.replace(placeholder, str(var_value))

        return sql

    def learn(self, question: str, sql: str, tags: list[str] = None) -> Optional[str]:
        """
        从历史查询中学习新模板

        Args:
            question: 用户问题
            sql: 生成的 SQL
            tags: 业务标签

        Returns:
            新模板名称或 None
        """
        if not self.auto_learn or not sql:
            return None

        with self._lock:
            # 检查是否已存在相似模板
            for template in self._templates.values():
                similarity = self._compute_similarity(sql, template.sql_template)
                if similarity > 0.85:
                    template.confidence = min(1.0, template.confidence + 0.05)
                    return template.name

            # 泛化 SQL
            generalized, variables = self._generalize_sql(sql)
            name = f"auto_learned_{len(self._templates)}"

            # 提取关键词作为 pattern
            keywords = re.findall(r'\w{2,}', question.lower())
            pattern = " ".join(keywords[:10])

            template = SQLTemplate(
                name=name,
                pattern=pattern,
                sql_template=generalized,
                description=f"从问题自动学习: {question[:50]}",
                variables=list(variables.keys()),
                tags=tags or [],
                confidence=0.7,
                created_at=__import__("time").time(),
            )
            self._templates[name] = template
            self._stats["learned"] += 1
            self._save_to_db(template)

            return name

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "learned": self._stats["learned"],
                "templates": len(self._templates),
                "top_templates": sorted(
                    [{"name": t.name, "hits": t.hit_count, "confidence": t.confidence}
                     for t in self._templates.values()],
                    key=lambda x: -x["hits"]
                )[:10],
            }


GLOBAL_TEMPLATE_ENGINE = TemplateEngine()