"""
Node 4: SQL 生成节点
规则式 SQL 生成（服务端不调用 LLM；外部 MCP 客户端可用 execute_sql 覆盖）

本节点只负责"给不含 LLM 的内置演示链路一个可跑的 SQL"。调用方若需要精确 SQL，
应自行生成后走 execute_sql。因此这里的规则保持浅显：识别时间窗口 + 主题维度。
"""
from __future__ import annotations
import re
import time
from graph.state import AgentState
from compressor.engine import GLOBAL_HEADROOM

# (关键词, 表, 默认时间跨度天数, 分组列, 度量表达式, 输出别名)
_TOPICS = (
    (("到店", "到访", "客流", "visit", "traffic"), "dwd_traffic_visit_di",
     "SUM(t.visit_count) AS visit_count", ("dt",)),
    (("销售", "金额", "订单", "sale", "order", "gmv"), "dwd_sale_order_di",
     "SUM(t.sale_amount) AS total_amount", ("dt",)),
    (("销量", "数量", "件数", "qty"), "dwd_sale_order_di",
     "SUM(t.sale_qty) AS total_qty", ("dt",)),
    (("客户", "会员", "customer"), "dwd_customer_visit_di",
     "COUNT(DISTINCT t.customer_id) AS customer_count", ("dt",)),
)

_REGION_DIMS = ("区域", "大区", "region", "地区")
_STORE_DIMS = ("门店", "店铺", "各店", "分店", "store", "shop")
_PRODUCT_DIMS = ("产品", "商品", "品类", "product", "category")

# 这些词里含有维度字（"到店"含"店"、"订单"含"单"），做维度判断前先剔除
_TOPIC_NOISE = ("到店", "到访", "客流", "下单", "订单", "排行")

_CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_UNIT_DAYS = {"天": 1, "日": 1, "周": 7, "月": 30, "季": 90, "年": 365}

_WINDOW_RE = re.compile(
    r"(?:最近|近|过去|前)\s*([0-9]+|[一二两三四五六七八九十]+)\s*(?:个|整|来)?\s*(天|日|周|月|季|年)"
)


def sql_generation_node(state: AgentState) -> dict:
    """SQL 生成节点"""
    question = state.get("question", "")
    intent = state.get("intent", "general")

    sql = _rule_generate_sql(question, intent)

    compressed = GLOBAL_HEADROOM.compress("sql", sql)

    return {
        "sql": compressed.data if GLOBAL_HEADROOM.enabled else sql,
        "sql_source": "rule",
        "sql_generation_error": "",
    }


def _parse_window_days(question: str) -> int:
    """
    解析问题里的时间窗口（"最近30天"/"近三个月"/"过去1年"），默认 30 天。

    未识别到窗口时用 30 天，而不是硬编码一个与问题无关的跨度。
    """
    match = _WINDOW_RE.search(question)
    if not match:
        return 30
    raw, unit = match.group(1), match.group(2)
    if raw.isdigit():
        amount = int(raw)
    else:
        amount = _CN_NUMBERS.get(raw, 1)
    return max(1, amount * _UNIT_DAYS[unit])


def _pick_dimension(question: str) -> str:
    """按问题选择分组维度；没有问题指明维度时返回空串（表示按时间看趋势）"""
    cleaned = question.lower()
    for noise in _TOPIC_NOISE:
        cleaned = cleaned.replace(noise.lower(), "")
    if any(word in cleaned for word in _REGION_DIMS):
        return "region"
    if any(word in cleaned for word in _PRODUCT_DIMS):
        return "product_name"
    if any(word in cleaned for word in _STORE_DIMS):
        return "store_name"
    return ""


def _rule_generate_sql(question: str, intent: str) -> str:
    """
    规则式生成 SQL：时间窗口与维度都来自问题本身。

    - 问题点了维度（区域/门店/产品）→ 按维度聚合排名
    - 没点维度 → 按时间聚合趋势（跨度 > 60 天用月粒度）
    这样一次问答不会返回几百行日×店的明细，结果集始终适合直接展示。
    """
    window_days = _parse_window_days(question)
    dimension = _pick_dimension(question)

    table, measure = "dwd_sale_order_di", "SUM(t.sale_amount) AS total_amount"
    for keywords, candidate_table, candidate_measure, _dimensions in _TOPICS:
        if any(keyword in question.lower() for keyword in keywords):
            table, measure = candidate_table, candidate_measure
            break

    if dimension == "product_name" and table != "dwd_sale_order_di":
        dimension = "store_name"  # 流量表没有产品维度

    metric = measure.split(" AS ")[0]

    if dimension:
        limit = 10 if any(w in question for w in ("排名", "top", "前")) else 50
        return (
            f"SELECT t.{dimension}, {measure}\n"
            f"FROM {table} t\n"
            f"WHERE t.dt >= date_sub(current_date(), {window_days})\n"
            f"GROUP BY t.{dimension}\n"
            f"ORDER BY {metric} DESC\n"
            f"LIMIT {limit}"
        )

    time_column = "t.par_month" if window_days > 60 else "t.dt"
    return (
        f"SELECT {time_column}, {measure}\n"
        f"FROM {table} t\n"
        f"WHERE t.dt >= date_sub(current_date(), {window_days})\n"
        f"GROUP BY {time_column}\n"
        f"ORDER BY {time_column}"
    )
