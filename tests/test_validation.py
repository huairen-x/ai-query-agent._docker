"""SQL 安全校验：词边界匹配回归（B1）"""
from graph.nodes.validation import _check_safety


def test_created_at_is_not_dangerous():
    assert _check_safety("SELECT created_at FROM dwd_sale_order_di")["safe"] is True


def test_string_literal_deleted_is_not_dangerous():
    assert _check_safety(
        "SELECT * FROM dwd_sale_order_di WHERE order_id = 'deleted'")["safe"] is True


def test_string_literal_update_is_not_dangerous():
    assert _check_safety(
        "SELECT * FROM dwd_sale_order_di WHERE dt = 'please update later'")["safe"] is True


def test_drop_statement_is_dangerous():
    assert _check_safety("DROP TABLE dwd_sale_order_di")["safe"] is False


def test_multi_statement_with_drop_is_dangerous():
    assert _check_safety("SELECT 1; DROP TABLE dwd_sale_order_di")["safe"] is False
