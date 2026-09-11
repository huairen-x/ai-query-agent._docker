"""
Mock 数据仓库 —— 无 Hive 环境下的数据源适配层

为什么不是"假执行"：
    旧实现按中文关键词返回硬编码结果集，完全无视调用方给的 SQL。
    本模块改用内存 SQLite 真实执行 SQL：GROUP BY / JOIN / 子查询 / CTE /
    ORDER BY / LIMIT / 聚合 全部按 SQL 语义出结果，外部 LLM（Trae）自己
    生成的 Hive SQL 可以直接跑通。表结构与 seed 数据由 CATALOG + 固定种子
    生成，metadata 工具、列类型推断、查询执行三者共用同一份真源。

可复现性：
    - mock 时钟固定为 MOCK_TODAY（2025-01-01），数据区间 2024-01-01 ~ 2024-12-31，
      因此 "最近 7/30/365 天" 这类相对日期查询永远有数据，结果不随真实日期漂移。
    - seed 固定，同样的 SQL 永远得到同样的结果。

Hive 兼容：
    date_sub / date_add / current_date / nvl / concat_ws / substring / year /
    month / day / to_date / date_format / datediff 均注册为 UDF。
    不支持 Hive 的 CAST(x AS string) 语法糖、date '...' 字面量、窗口函数之外的
    Hive 专有语法；调不到的函数会让 SQLite 直接报错而不是静默返回错数据。
"""
from __future__ import annotations

import datetime as dt
import math
import os
import random
import re
import sqlite3
import threading

# ── mock 时钟与数据区间 ──────────────────────────────────────
MOCK_TODAY = "2025-01-01"
DATA_START = dt.date(2024, 1, 1)
DATA_DAYS = 366  # 2024 全年（闰年）
MOCK_SEED = 20240101
MAX_ROWS = int(os.environ.get("MOCK_MAX_ROWS", "500"))

# 单条查询的 VM 指令上限：拦截漏写 JOIN 条件导致的天量笛卡尔积
_MAX_VM_STEPS = 20_000_000


class WarehouseError(Exception):
    """SQL 执行失败（语法/未知表/未知函数/触发保护）"""


# ============================================================
# 表结构真源（catalog）
# metadata 工具、列注释、类型推断全部取自这里
# ============================================================
CATALOG: dict[str, dict] = {
    "dwd_sale_order_di": {
        "comment": "销售订单明细（按日分区，一行一笔订单）",
        "partition_keys": ["dt"],
        "columns": [
            ("order_id", "string", "订单ID"),
            ("dt", "string", "分区日期 yyyy-MM-dd"),
            ("par_month", "string", "分区月份 yyyy-MM"),
            ("store_code", "string", "门店编码"),
            ("store_name", "string", "门店名称"),
            ("region", "string", "区域"),
            ("product_code", "string", "产品编码"),
            ("product_name", "string", "产品名称"),
            ("sale_qty", "int", "销售数量"),
            ("sale_amount", "decimal(18,2)", "销售金额"),
            ("customer_id", "string", "客户ID"),
        ],
    },
    "dwd_sale_order_item_di": {
        "comment": "销售订单行项目（一行一个商品行）",
        "partition_keys": ["dt"],
        "columns": [
            ("line_id", "string", "行项目ID"),
            ("order_id", "string", "订单ID"),
            ("dt", "string", "分区日期 yyyy-MM-dd"),
            ("par_month", "string", "分区月份 yyyy-MM"),
            ("store_code", "string", "门店编码"),
            ("store_name", "string", "门店名称"),
            ("region", "string", "区域"),
            ("product_code", "string", "产品编码"),
            ("product_name", "string", "产品名称"),
            ("sale_qty", "int", "销售数量"),
            ("sale_amount", "decimal(18,2)", "行项目金额"),
        ],
    },
    "dim_product_df": {
        "comment": "产品维度表（全量快照）",
        "partition_keys": [],
        "columns": [
            ("product_code", "string", "产品编码"),
            ("product_name", "string", "产品名称"),
            ("category", "string", "品类"),
            ("brand", "string", "品牌线"),
            ("price", "decimal(18,2)", "标准售价"),
        ],
    },
    "dim_store_df": {
        "comment": "门店维度表（全量快照）",
        "partition_keys": [],
        "columns": [
            ("store_code", "string", "门店编码"),
            ("store_name", "string", "门店名称"),
            ("region", "string", "区域"),
            ("city", "string", "城市"),
            ("open_date", "string", "开业日期 yyyy-MM-dd"),
        ],
    },
    "dwd_traffic_visit_di": {
        "comment": "到店流量明细（按日分区，一行一店一天）",
        "partition_keys": ["dt"],
        "columns": [
            ("dt", "string", "分区日期 yyyy-MM-dd"),
            ("par_month", "string", "分区月份 yyyy-MM"),
            ("store_code", "string", "门店编码"),
            ("store_name", "string", "门店名称"),
            ("region", "string", "区域"),
            ("visit_count", "int", "到店人次"),
            ("customer_count", "int", "到店客户数"),
        ],
    },
    "dwd_customer_visit_di": {
        "comment": "客户到访明细（按日分区）",
        "partition_keys": ["dt"],
        "columns": [
            ("dt", "string", "分区日期 yyyy-MM-dd"),
            ("par_month", "string", "分区月份 yyyy-MM"),
            ("customer_id", "string", "客户ID"),
            ("store_code", "string", "门店编码"),
            ("store_name", "string", "门店名称"),
            ("region", "string", "区域"),
            ("visit_count", "int", "到访次数"),
            ("duration_min", "int", "停留时长（分钟）"),
        ],
    },
}

# 列名 → (类型, 注释)，用于给查询结果补列元信息
_COLUMN_INDEX: dict[str, tuple[str, str]] = {}
for _t in CATALOG.values():
    for _name, _type, _comment in _t["columns"]:
        _COLUMN_INDEX.setdefault(_name, (_type, _comment))


# ============================================================
# 维表 seed
# ============================================================
_STORES = [
    ("S001", "北京旗舰店", "华北", "北京"),
    ("S002", "北京朝阳店", "华北", "北京"),
    ("S003", "天津和平店", "华北", "天津"),
    ("S004", "石家庄中山店", "华北", "石家庄"),
    ("S005", "上海南京路店", "华东", "上海"),
    ("S006", "上海徐汇店", "华东", "上海"),
    ("S007", "杭州西湖店", "华东", "杭州"),
    ("S008", "南京新街口店", "华东", "南京"),
    ("S009", "苏州观前店", "华东", "苏州"),
    ("S010", "广州天河店", "华南", "广州"),
    ("S011", "深圳福田店", "华南", "深圳"),
    ("S012", "厦门思明店", "华南", "厦门"),
    ("S013", "福州鼓楼店", "华南", "福州"),
    ("S014", "成都春熙店", "西南", "成都"),
    ("S015", "重庆解放碑店", "西南", "重庆"),
    ("S016", "昆明南屏店", "西南", "昆明"),
    ("S017", "西安钟楼店", "西北", "西安"),
    ("S018", "兰州张掖路店", "西北", "兰州"),
    ("S019", "乌鲁木齐天山店", "西北", "乌鲁木齐"),
    ("S020", "银川解放店", "西北", "银川"),
]

_PRODUCTS = [
    ("P001", "经典款T恤", "服装", "basic", 129.0),
    ("P002", "休闲牛仔裤", "服装", "basic", 299.0),
    ("P003", "运动鞋", "鞋履", "sport", 499.0),
    ("P004", "羽绒服", "服装", "warm", 899.0),
    ("P005", "棒球帽", "配饰", "basic", 89.0),
    ("P006", "真丝衬衫", "服装", "premium", 599.0),
    ("P007", "商务皮鞋", "鞋履", "premium", 799.0),
    ("P008", "羊绒围巾", "配饰", "warm", 399.0),
    ("P009", "户外冲锋衣", "服装", "outdoor", 1099.0),
    ("P010", "跑步短裤", "服装", "sport", 199.0),
    ("P011", "帆布鞋", "鞋履", "basic", 259.0),
    ("P012", "手提包", "配饰", "premium", 1299.0),
    ("P013", "针织开衫", "服装", "warm", 459.0),
    ("P014", "登山靴", "鞋履", "outdoor", 899.0),
    ("P015", "皮带", "配饰", "basic", 159.0),
    ("P016", "速干T恤", "服装", "sport", 169.0),
    ("P017", "防晒衣", "服装", "outdoor", 329.0),
    ("P018", "乐福鞋", "鞋履", "premium", 659.0),
    ("P019", "太阳镜", "配饰", "outdoor", 299.0),
    ("P020", "羽绒马甲", "服装", "warm", 699.0),
]

# 区域规模系数：让区域对比有稳定差异，而不是随机噪声
_REGION_SCALE = {"华北": 1.00, "华东": 1.35, "华南": 1.15, "西南": 0.85, "西北": 0.60}


# ============================================================
# Hive 风格 UDF
# ============================================================
def _to_date(value) -> dt.date | None:
    """把 'yyyy-MM-dd'/'yyyy-MM-dd HH:MM:SS' 解析为 date；不可解析返回 None"""
    if value is None:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%Y-%m"):
        try:
            return dt.datetime.strptime(text[:19] if ":" in text else text, fmt).date()
        except ValueError:
            continue
    return None


_DATE_FORMAT_MAP = [
    ("yyyy-MM-dd", "%Y-%m-%d"),
    ("yyyy-MM", "%Y-%m"),
    ("yyyyMMdd", "%Y%m%d"),
    ("yyyyMM", "%Y%m"),
    ("yyyy/MM/dd", "%Y/%m/%d"),
    ("yyyy", "%Y"),
    ("MM", "%m"),
    ("dd", "%d"),
]


def _register_functions(conn: sqlite3.Connection) -> None:
    """注册常用 Hive 函数，让外部 LLM 生成的 Hive SQL 可直接执行"""

    def current_date():
        return MOCK_TODAY

    def date_sub(value, days):
        base = _to_date(value) or _to_date(MOCK_TODAY)
        if days is None:
            return None
        return (base - dt.timedelta(days=int(days))).isoformat()

    def date_add(value, days):
        base = _to_date(value) or _to_date(MOCK_TODAY)
        if days is None:
            return None
        return (base + dt.timedelta(days=int(days))).isoformat()

    def datediff(end, start):
        a, b = _to_date(end), _to_date(start)
        return None if a is None or b is None else (a - b).days

    def nvl(value, fallback):
        return fallback if value is None else value

    def concat(*args):
        return "".join("" if a is None else str(a) for a in args)

    def concat_ws(sep, *args):
        if sep is None:
            return None
        return str(sep).join(str(a) for a in args if a is not None)

    def substring(value, start, length=None):
        if value is None or start is None:
            return None
        text = str(value)
        begin = int(start) - 1  # Hive 下标从 1 开始
        if begin < 0:
            begin = max(0, len(text) + begin)
        return text[begin:] if length is None else text[begin:begin + int(length)]

    def to_date(value):
        parsed = _to_date(value)
        return parsed.isoformat() if parsed else None

    def year(value):
        parsed = _to_date(value)
        return parsed.year if parsed else None

    def month(value):
        parsed = _to_date(value)
        return parsed.month if parsed else None

    def day(value):
        parsed = _to_date(value)
        return parsed.day if parsed else None

    def date_format(value, fmt):
        parsed = _to_date(value)
        if parsed is None or fmt is None:
            return None
        translated = str(fmt)
        for hive_fmt, py_fmt in _DATE_FORMAT_MAP:
            translated = translated.replace(hive_fmt, py_fmt)
        try:
            return parsed.strftime(translated)
        except ValueError:
            return None

    conn.create_function("date_sub", 2, date_sub)
    conn.create_function("date_add", 2, date_add)
    conn.create_function("datediff", 2, datediff)
    conn.create_function("nvl", 2, nvl)
    conn.create_function("concat", -1, concat)
    conn.create_function("concat_ws", -1, concat_ws)
    conn.create_function("substring", -1, substring)
    conn.create_function("to_date", 1, to_date)
    conn.create_function("year", 1, year)
    conn.create_function("month", 1, month)
    conn.create_function("day", 1, day)
    conn.create_function("date_format", 2, date_format)


# ============================================================
# 数据生成
# ============================================================
def _build_tables(conn: sqlite3.Connection) -> None:
    """建表并灌入确定性 seed 数据"""
    conn.execute("""
        CREATE TABLE dwd_sale_order_di (
            order_id TEXT, dt TEXT, par_month TEXT,
            store_code TEXT, store_name TEXT, region TEXT,
            product_code TEXT, product_name TEXT,
            sale_qty INTEGER, sale_amount REAL, customer_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE dwd_sale_order_item_di (
            line_id TEXT, order_id TEXT, dt TEXT, par_month TEXT,
            store_code TEXT, store_name TEXT, region TEXT,
            product_code TEXT, product_name TEXT,
            sale_qty INTEGER, sale_amount REAL
        )
    """)
    conn.execute("""
        CREATE TABLE dim_product_df (
            product_code TEXT, product_name TEXT, category TEXT,
            brand TEXT, price REAL
        )
    """)
    conn.execute("""
        CREATE TABLE dim_store_df (
            store_code TEXT, store_name TEXT, region TEXT,
            city TEXT, open_date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE dwd_traffic_visit_di (
            dt TEXT, par_month TEXT, store_code TEXT, store_name TEXT,
            region TEXT, visit_count INTEGER, customer_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE dwd_customer_visit_di (
            dt TEXT, par_month TEXT, customer_id TEXT, store_code TEXT,
            store_name TEXT, region TEXT, visit_count INTEGER, duration_min INTEGER
        )
    """)

    rng = random.Random(MOCK_SEED)
    orders, items, traffic, customer_visits = [], [], [], []

    for day_index in range(DATA_DAYS):
        current = DATA_START + dt.timedelta(days=day_index)
        day = current.isoformat()
        par_month = day[:7]
        weekday = current.weekday()
        # 季节波动 + 周末放大：让趋势/对比类查询有真实的形状
        seasonal = 1.0 + 0.30 * math.sin(day_index / 365.0 * 2 * math.pi)
        weekend = 1.25 if weekday >= 5 else 1.0
        dow = 0.92 + 0.03 * weekday

        for store_code, store_name, region, _city in _STORES:
            scale = _REGION_SCALE[region] * seasonal * weekend * dow * rng.uniform(0.85, 1.15)

            for line in range(2):
                product_code, product_name, _cat, _brand, price = _PRODUCTS[
                    rng.randrange(len(_PRODUCTS))
                ]
                qty = rng.randint(1, 6)
                amount = round(price * qty * scale, 2)
                order_id = f"O{day_index:03d}{store_code}{line:02d}"
                orders.append((
                    order_id, day, par_month, store_code, store_name, region,
                    product_code, product_name, qty, amount,
                    f"C{rng.randrange(100000, 999999)}",
                ))
                items.append((
                    f"{order_id}-1", order_id, day, par_month, store_code,
                    store_name, region, product_code, product_name, qty, amount,
                ))

            visits = int(180 * scale + rng.randint(-20, 20))
            customers = int(visits * rng.uniform(0.62, 0.82))
            traffic.append((day, par_month, store_code, store_name, region,
                            max(visits, 0), max(customers, 0)))
            customer_visits.append((
                day, par_month, f"C{rng.randrange(100000, 999999)}", store_code,
                store_name, region, rng.randint(1, 3), rng.randint(5, 120),
            ))

    conn.executemany("INSERT INTO dwd_sale_order_di VALUES (?,?,?,?,?,?,?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO dwd_sale_order_item_di VALUES (?,?,?,?,?,?,?,?,?,?,?)", items)
    conn.executemany("INSERT INTO dim_product_df VALUES (?,?,?,?,?)", [
        (code, name, category, brand, float(price))
        for code, name, category, brand, price in _PRODUCTS
    ])
    conn.executemany("INSERT INTO dim_store_df VALUES (?,?,?,?,?)", [
        (code, name, region, city,
         (dt.date(2015, 1, 1) + dt.timedelta(days=index * 97)).isoformat())
        for index, (code, name, region, city) in enumerate(_STORES)
    ])
    conn.executemany("INSERT INTO dwd_traffic_visit_di VALUES (?,?,?,?,?,?,?)", traffic)
    conn.executemany("INSERT INTO dwd_customer_visit_di VALUES (?,?,?,?,?,?,?,?)", customer_visits)

    for table, keys in (
        ("dwd_sale_order_di", ("dt", "store_code")),
        ("dwd_sale_order_item_di", ("dt", "store_code")),
        ("dwd_traffic_visit_di", ("dt", "store_code")),
        ("dwd_customer_visit_di", ("dt", "store_code")),
    ):
        for column in keys:
            conn.execute(f"CREATE INDEX idx_{table}_{column} ON {table}({column})")

    conn.commit()


# ============================================================
# 数仓
# ============================================================
class MockWarehouse:
    """内存 SQLite 承载的 Hive 风格 mock 数仓"""

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._vm_steps = 0
        self._table_names = tuple(CATALOG)

    # ── 连接与单次构建 ────────────────────────────────────
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    conn = sqlite3.connect(
                        ":memory:", check_same_thread=False, isolation_level=None
                    )
                    _register_functions(conn)
                    _build_tables(conn)
                    conn.set_progress_handler(self._vm_guard, 10_000)
                    conn.execute("PRAGMA query_only = ON")  # 执行阶段强制只读
                    self._conn = conn
        return self._conn

    def _vm_guard(self) -> int:
        self._vm_steps += 10_000
        return 1 if self._vm_steps > _MAX_VM_STEPS else 0

    # ── 元数据 ────────────────────────────────────────────
    def table_names(self) -> tuple[str, ...]:
        return self._table_names

    def describe(self, table_name: str) -> dict:
        """表结构；未知表名抛 WarehouseError"""
        table = CATALOG.get(table_name)
        if table is None:
            raise WarehouseError(
                f"未知表: {table_name}；可用表: {', '.join(self._table_names)}"
            )
        conn = self._connection()
        with self._lock:
            row_count = conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]
        return {
            "table_name": table_name,
            "comment": table["comment"],
            "columns": [
                {"name": name, "type": type_, "comment": comment}
                for name, type_, comment in table["columns"]
            ],
            "partition_keys": list(table["partition_keys"]),
            "table_type": "MANAGED_TABLE",
            "row_count": row_count,
        }

    def search(self, keyword: str) -> list[dict]:
        """按表名或表注释模糊匹配"""
        needle = (keyword or "").strip().lower()
        if not needle:
            return self.list_tables()
        return [
            {"table_name": name, "comment": table["comment"]}
            for name, table in CATALOG.items()
            if needle in name.lower() or needle in table["comment"].lower()
        ]

    def list_tables(self) -> list[dict]:
        """全部表（表名 + 注释）；不确定表名时的首轮探查入口"""
        return [
            {"table_name": name, "comment": CATALOG[name]["comment"]}
            for name in self._table_names
        ]

    # ── 查询执行 ──────────────────────────────────────────
    def execute(self, sql: str, max_rows: int = MAX_ROWS) -> dict:
        """
        执行一条只读 SQL，返回 {rows, columns, row_count, truncated}

        与传统 mock 不同：这里是真实执行，SQL 写错/表名写错会抛 WarehouseError，
        不会返回看似合理但与 SQL 无关的假数据。
        """
        statement = _normalize_single_select(sql)
        conn = self._connection()
        with self._lock:
            self._vm_steps = 0
            try:
                cursor = conn.execute(statement)
                names = [d[0] for d in (cursor.description or [])]
                fetched = cursor.fetchmany(max_rows + 1)
            except sqlite3.Error as exc:
                raise WarehouseError(f"{type(exc).__name__}: {exc}") from exc

        truncated = len(fetched) > max_rows
        rows = [dict(zip(names, values)) for values in fetched[:max_rows]]
        return {
            "rows": rows,
            "columns": _columns_meta(names, rows),
            "row_count": len(rows),
            "truncated": truncated,
        }


def _normalize_single_select(sql: str) -> str:
    """只允许单条 SELECT/WITH，去掉尾分号，并把 mock 时钟字面量化"""
    if not sql or not sql.strip():
        raise WarehouseError("SQL 为空")

    statement = sql.strip()
    # 去掉注释行，避免把注释里的关键字当语句
    statement = re.sub(r"^\s*--.*$", "", statement, flags=re.MULTILINE).strip()
    statement = statement.rstrip(";").strip()

    if ";" in statement:
        raise WarehouseError("只允许执行单条语句")

    head = statement.split(None, 1)[0].lower() if statement.split() else ""
    if head not in ("select", "with"):
        raise WarehouseError(f"只允许 SELECT/WITH 只读查询，收到: {head}")

    statement = _substitute_mock_clock(statement)

    # SQLite 会把 '2025-01-01' - 30 静默算成 1995，导致 dt 比较恒真。
    # 与其返回看似合理的错数据，不如直接报错要求用 Hive 的 date_sub/date_add。
    if re.search(r"'\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?'\s*[-+]\s*\d", statement):
        raise WarehouseError(
            "不支持日期字面量直接加减，请使用 date_sub(date, n) / date_add(date, n)"
        )

    return statement


def _substitute_mock_clock(sql: str) -> str:
    """
    current_date() / current_timestamp() 字面量化为 MOCK_TODAY。

    SQLite 把 CURRENT_DATE 当保留字，`current_date()` 会直接语法错误；
    而不加括号的 CURRENT_DATE 又会返回真实系统日期（2026-xx-xx），
    让 "最近 N 天" 类查询落空。统一替换成 mock 时钟，两处坑一起绕开。
    只在字符串字面量之外替换。
    """
    segments = re.split(r"('(?:[^']|'')*')", sql)
    for index in range(0, len(segments), 2):
        segments[index] = re.sub(
            r"(?i)\bcurrent_date\b\s*(?:\(\s*\))?", f"'{MOCK_TODAY}'", segments[index]
        )
        segments[index] = re.sub(
            r"(?i)\bcurrent_timestamp\b\s*(?:\(\s*\))?",
            f"'{MOCK_TODAY} 00:00:00'",
            segments[index],
        )
    return "".join(segments)


def _columns_meta(names: list[str], rows: list[dict]) -> list[dict]:
    """列元信息：优先取 catalog 注释，派生列按实际值推断类型"""
    columns = []
    for name in names:
        known = _COLUMN_INDEX.get(name)
        if known:
            type_, comment = known
        else:
            type_, comment = _infer_type(name, rows), ""
        columns.append({"name": name, "type": type_, "comment": comment})
    return columns


def _infer_type(name: str, rows: list[dict]) -> str:
    for row in rows:
        value = row.get(name)
        if value is None:
            continue
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "double"
        if isinstance(value, bytes):
            return "binary"
        return "string"
    return "string"


# 全局单例：整个进程共用一个数仓
GLOBAL_WAREHOUSE = MockWarehouse()
