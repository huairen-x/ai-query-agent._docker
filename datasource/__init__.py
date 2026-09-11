"""
数据源适配层

当前环境没有 Hive，默认使用 MockWarehouse（内存 SQLite 承载 Hive 风格表）。
后续接真实 Hive 时，只需在 gateway / 节点里换掉 MockWarehouse 的实现，
CATALOG 与 execute/describe/search 三个接口保持不变。
"""
from datasource.mock_warehouse import (
    CATALOG,
    MOCK_TODAY,
    MockWarehouse,
    WarehouseError,
    GLOBAL_WAREHOUSE,
)

__all__ = [
    "CATALOG",
    "MOCK_TODAY",
    "MockWarehouse",
    "WarehouseError",
    "GLOBAL_WAREHOUSE",
]
