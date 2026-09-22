"""商品模块用例：列表、分页边界、详情一致性、负向查询、响应结构、耗时。"""

import pytest

from api.product_api import ProductApi
from core.assertions import (
    assert_field,
    assert_response_time,
    assert_status,
)

pytestmark = [pytest.mark.regression]


@pytest.mark.smoke
def test_product_list_returns_array(client, require_sut):
    """正向：商品列表返回数组且非空。

    注意 WooCommerce 的列表接口直接返回 JSON 数组（不是 {"products": [...]}），
    这与很多被测系统的响应结构不同——切靶子时必须先核实响应结构。
    """
    record = ProductApi(client).list_products(per_page=5)
    assert_status(record, 200)
    assert isinstance(record.response_body, list), (
        f"列表接口应返回数组，实际类型 {type(record.response_body).__name__}"
    )
    assert len(record.response_body) > 0, "商品列表为空，用例前提不成立"


@pytest.mark.smoke
def test_product_list_exposes_total_count_header(client, require_sut):
    """正向：分页总数通过 X-WP-Total 响应头返回，必须存在且为非负整数。"""
    record = ProductApi(client).list_products(per_page=1)
    assert_status(record, 200)
    total = record.total_count
    assert total is not None, f"缺少 X-WP-Total 响应头：{record.response_headers}"
    assert total >= 0


def test_per_page_is_respected(client, require_sut):
    """边界：per_page=1 只能返回 1 条，分页参数不能被静默忽略。"""
    record = ProductApi(client).list_products(per_page=1)
    assert_status(record, 200)
    assert len(record.response_body) == 1, "per_page 参数被忽略"


def test_per_page_upper_bound_is_enforced(client, require_sut):
    """边界（上界）：per_page 超过 100 必须被参数校验拒绝。

    预期依据 WP REST API 的参数范围校验（per_page 取值 1-100）。
    首次实跑需确认返回码与错误码，确认后把结论写入用例文档。
    """
    record = ProductApi(client).list_products(per_page=101)
    assert record.status_code == 400, (
        f"per_page=101 未被拒绝，实际 {record.status_code}：{record.summary}"
    )


def test_page_beyond_range_returns_empty_list(client, require_sut):
    """边界（越界）：页码超过总页数时返回 200 + 空数组。

    实测修正（2026-09-22）：原本预期越界页码返回 400，实际返回 200 + []。
    这属于合理设计（越界页就是没有数据），因此把断言改为"空数组"，
    而不是继续按错误预期要求 400。
    """
    record = ProductApi(client).list_products(per_page=1, page=99999)
    assert_status(record, 200)
    assert record.response_body == [], (
        f"越界页码应返回空数组，实际返回 {len(record.response_body)} 条数据"
    )


def test_product_detail_matches_list_item(client, require_sut):
    """交叉校验：详情中的 id 与 name 必须与列表项一致（防止列表/详情数据源不一致）。"""
    api = ProductApi(client)
    listed = api.list_products(per_page=1).response_body[0]
    record = api.get_product(int(listed["id"]))
    assert_status(record, 200)
    assert_field(record, "id", listed["id"])
    assert_field(record, "name", listed["name"])


def test_missing_product_returns_404(client, require_sut):
    """负向：不存在的商品必须 404，不能返回 200 + 空对象。"""
    record = ProductApi(client).get_missing_product()
    assert_status(record, 404)


def test_product_list_response_time(client, settings, require_sut):
    """性能：列表接口耗时不得超过配置阈值。"""
    record = ProductApi(client).list_products(per_page=10)
    assert_status(record, 200)
    assert_response_time(record, settings.thresholds.get("response_time_ms", 3000))
