"""订单链路用例：创建、读回校验、优惠券折扣勾稽、状态流转、数据清理验证。

所有写数据的用例都通过 temporary_test_data 上下文管理器登记清理动作，
退出时（含断言失败）在 finally 中删除测试数据 —— 保证用例可以重复运行。
"""

import pytest

from business.order_flow import temporary_test_data
from core.assertions import assert_status

pytestmark = [pytest.mark.regression]


@pytest.mark.smoke
def test_create_order_success(flow, require_sut):
    """正向：用已上架商品创建订单，必须返回订单号与初始状态。"""
    with temporary_test_data(flow) as tracker:
        product_id = flow.require_published_product()
        order = flow.create_order(tracker, product_id)
        assert order.get("id"), "创建订单未返回订单号"
        assert order.get("status"), "创建订单未返回状态"


@pytest.mark.smoke
def test_created_order_can_be_read_back(flow, require_sut):
    """读回校验：创建接口返回成功 ≠ 数据真的落库，必须回查确认。"""
    with temporary_test_data(flow) as tracker:
        product_id = flow.require_published_product()
        created = flow.create_order(tracker, product_id)
        record = flow.orders.get_order(int(created["id"]))
        assert_status(record, 200)
        assert record.response_body["id"] == created["id"]


def test_order_amount_composition(flow, require_sut):
    """金额勾稽：无折扣订单的总额必须等于行项目折后合计 + 运费 + 税。"""
    with temporary_test_data(flow) as tracker:
        product_id = flow.require_published_product()
        order = flow.create_order(tracker, product_id, quantity=2)
        flow.assert_total_composition(order)
        flow.assert_total_not_negative(order)
        flow.assert_discount_matches_line_items(order)


def test_order_with_coupon_applies_discount(flow, require_sut):
    """业务校验：带优惠券下单必须真的产生折扣，且总额小于小计。"""
    with temporary_test_data(flow) as tracker:
        product_id = flow.require_published_product()
        coupon = flow.create_coupon(tracker, percent="10")
        order = flow.create_order(tracker, product_id, coupon_code=coupon["code"])

        discount = flow.discount_total(order)
        assert discount > 0, f"使用了优惠券但折扣为 0：订单 {order.get('id')}"
        assert float(order["total"]) < flow.line_items_subtotal(order), (
            "折扣生效但订单总额未小于行项目原价合计"
        )
        flow.assert_discount_matches_line_items(order)
        flow.assert_total_composition(order)


def test_coupon_usage_is_recorded_on_order(flow, require_sut):
    """读回校验：订单上必须能看到使用的优惠券编码（防止券生效但无留痕）。"""
    with temporary_test_data(flow) as tracker:
        product_id = flow.require_published_product()
        coupon = flow.create_coupon(tracker, percent="5")
        order = flow.create_order(tracker, product_id, coupon_code=coupon["code"])

        codes = [line.get("code") for line in order.get("coupon_lines", [])]
        assert coupon["code"] in codes, (
            f"订单上未记录使用的优惠券，期望 {coupon['code']}，实际 {codes}"
        )


def test_order_status_transition(flow, require_sut):
    """状态机：订单状态变更后必须能读回新状态（接口返回成功 ≠ 真的生效）。"""
    with temporary_test_data(flow) as tracker:
        product_id = flow.require_published_product()
        order = flow.create_order(tracker, product_id)

        record = flow.orders.update_status(int(order["id"]), "completed")
        assert_status(record, 200)
        assert record.response_body.get("status") == "completed"

        readback = flow.orders.get_order(int(order["id"]))
        assert readback.response_body.get("status") == "completed", (
            f"状态未持久化：读回得到 {readback.response_body.get('status')}"
        )


def test_test_data_is_cleaned_up(flow, require_sut):
    """数据卫生：用例自己清理掉创建的数据，保证回归可以重复运行。

    这条用例本身也是自检——如果清理逻辑失效，它会失败。
    """
    with temporary_test_data(flow) as tracker:
        product_id = flow.require_published_product()
        order = flow.create_order(tracker, product_id)
        order_id = int(order["id"])

    record = flow.orders.get_order(order_id)
    assert record.status_code == 404, (
        f"清理后订单 {order_id} 仍可访问，测试数据未被清理：{record.summary}"
    )
