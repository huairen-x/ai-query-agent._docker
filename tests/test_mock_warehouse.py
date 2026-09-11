"""Mock 数仓：SQL 语义与防护"""
import pytest

from datasource.mock_warehouse import GLOBAL_WAREHOUSE, WarehouseError


def test_group_by_region_aggregation():
    result = GLOBAL_WAREHOUSE.execute(
        "SELECT region, SUM(sale_amount) AS amt FROM dwd_sale_order_di "
        "GROUP BY region ORDER BY amt DESC"
    )
    assert result["row_count"] == 5
    assert result["rows"][0]["region"] == "华东"
    assert round(result["rows"][0]["amt"], 2) == 9636939.76


def test_relative_time_window_over_mock_clock():
    result = GLOBAL_WAREHOUSE.execute(
        "SELECT COUNT(DISTINCT dt) AS c FROM dwd_traffic_visit_di "
        "WHERE dt >= date_sub(current_date(), 7)"
    )
    assert result["rows"][0]["c"] == 7


def test_describe_columns_and_row_count():
    described = GLOBAL_WAREHOUSE.describe("dwd_sale_order_di")
    assert {"name": "sale_amount", "type": "decimal(18,2)", "comment": "销售金额"} in described["columns"]
    assert described["row_count"] == 14640


def test_search_all_and_by_chinese_keyword():
    assert len(GLOBAL_WAREHOUSE.search("")) == 6
    hits = GLOBAL_WAREHOUSE.search("到店")
    assert [h["table_name"] for h in hits] == ["dwd_traffic_visit_di"]


@pytest.mark.parametrize("sql", [
    "DROP TABLE dwd_sale_order_di",
    "SELECT 1; SELECT 2",
    "SELECT * FROM sales_fact",
    "SELECT COUNT(*) FROM dwd_traffic_visit_di WHERE dt >= '2025-01-01' - 30",
])
def test_rejects_unsafe_or_invalid_sql(sql):
    with pytest.raises(WarehouseError):
        GLOBAL_WAREHOUSE.execute(sql)
